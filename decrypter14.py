#!/usr/bin/env python3
"""
recuperar_backup_whatsapp.py — recuperação de backups locais do WhatsApp (Android)

  pip install pycryptodome
  adb devices            # aparelho conectado, depuração USB ligada, root concedido

  python recuperar_backup_whatsapp.py listar
  python recuperar_backup_whatsapp.py chave --destino ./key
  python recuperar_backup_whatsapp.py descriptografar --backup msgstore-2026-11-10.1.db.crypt14 --chave ./key --saida msgstore.db
  python recuperar_backup_whatsapp.py verificar --db msgstore.db
  python recuperar_backup_whatsapp.py restaurar --backup msgstore-2026-11-10.1.db.crypt14
"""
import argparse, os, re, sqlite3, subprocess, sys
from pathlib import Path

try:
    from Crypto.Cipher import AES
except ImportError:
    AES = None

DIRS_BACKUP = [
    "/sdcard/Android/media/com.whatsapp/WhatsApp/Databases",
    "/sdcard/WhatsApp/Databases",
    "/sdcard/Android/media/com.whatsapp/WhatsApp/Backups",
]
CHAVE_REMOTA = "/data/data/com.whatsapp/files/key"
MAGIC = b"SQLite format 3\x00"


def adb(args, binario=False):
    cmd = ["adb"] + args
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"adb falhou: {' '.join(cmd)}\n{r.stderr.decode(errors='ignore')}")
    return r.stdout if binario else r.stdout.decode(errors="ignore")


# ---------------------------------------------------------------- listar
def listar(pasta=None):
    if pasta:
        arquivos = sorted(Path(pasta).glob("msgstore*.crypt*"))
        if not arquivos:
            print("Nenhum backup nessa pasta.")
        for a in arquivos:
            print(f"  {a.name}  {a.stat().st_size/1048576:.1f} MB")
        return

    achou = False
    for d in DIRS_BACKUP:
        try:
            saida = adb(["shell", f"ls -l {d} 2>/dev/null"])
        except RuntimeError:
            continue
        linhas = [l for l in saida.splitlines() if re.search(r"msgstore.*\.crypt\d+", l)]
        if not linhas:
            continue
        achou = True
        print(f"\n== {d}")
        for l in sorted(linhas, key=lambda x: x.split()[-1]):
            p = l.split()
            print(f"  {p[-1]}  {int(p[4])/1048576:.1f} MB  {' '.join(p[5:8])}")
    if not achou:
        print("Nada encontrado. Confira 'adb devices', o root e as permissões de armazenamento.")


# ---------------------------------------------------------------- chave
def extrair_chave(destino: Path):
    try:
        blob = adb(["exec-out", "su", "-c", f"cat {CHAVE_REMOTA}"], binario=True)
    except RuntimeError:
        sys.exit("Falha ao ler a chave. O aparelho está com root ativo e o 'su' concedido ao shell?")
    if len(blob) < 32:
        sys.exit(f"Chave inválida ({len(blob)} bytes).")
    destino.write_bytes(blob)
    os.chmod(destino, 0o600)
    print(f"Chave salva em {destino} ({len(blob)} bytes)")


# ---------------------------------------------------------------- descriptografar
def tirar_padding(dados: bytes) -> bytes:
    n = dados[-1] if dados else 0
    if 1 <= n <= 16 and dados[-n:] == bytes([n]) * n:
        return dados[:-n]
    return dados.rstrip(b"\x00")


def montar_chave_iv(tipo, blob, header, corpo):
    def valida(chave, iv):
        return AES.new(chave, AES.MODE_CBC, iv).decrypt(corpo[:16]) == MAGIC

    if tipo == 15:
        if len(blob) < 99:
            sys.exit("Arquivo 'key' inválido para crypt15.")
        chave = blob[51:83]
        for iv in (blob[83:99], header[:16], header[1:17], bytes(16)):
            if valida(chave, iv):
                return chave, iv
        return chave, blob[83:99]

    if len(blob) < 32:
        sys.exit("Arquivo 'key' inválido.")
    chave = blob[:32]
    for iv in (chave[:16], header[:16], header[1:17], bytes(16)):
        if valida(chave, iv):
            return chave, iv
    return chave, chave[:16]


def descriptografar(backup: Path, chave_path: Path, saida: Path):
    if AES is None:
        sys.exit("Instale a dependência: pip install pycryptodome")
    m = re.search(r"\.crypt(\d+)$", backup.name)
    if not m:
        sys.exit("O arquivo precisa terminar em .crypt14 ou .crypt15")
    tipo = int(m.group(1))
    if tipo not in (14, 15):
        sys.exit(f"crypt{tipo} não suportado (apenas 14 e 15).")

    header_len = 67 if tipo == 14 else 51
    dados = backup.read_bytes()
    if len(dados) <= header_len + 32:
        sys.exit("Backup truncado ou vazio.")
    header, corpo = dados[:header_len], dados[header_len:]

    chave, iv = montar_chave_iv(tipo, chave_path.read_bytes(), header, corpo)
    claro = tirar_padding(AES.new(chave, AES.MODE_CBC, iv).decrypt(corpo))

    if not claro.startswith(MAGIC):
        sys.exit("Resultado não é um SQLite válido. A chave pertence a outra instalação/conta?")
    saida.write_bytes(claro)
    print(f"OK: {saida} ({saida.stat().st_size/1048576:.1f} MB)")


# ---------------------------------------------------------------- verificar
def verificar(db: Path):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    print("integrity_check:", con.execute("PRAGMA integrity_check").fetchone()[0])
    for t in ("chat", "message"):
        try:
            print(f"{t}: {con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]}")
        except sqlite3.Error as e:
            print(f"{t}: erro {e}")
    print("\nÚltimas mensagens:")
    for linha in con.execute(
        "SELECT datetime(timestamp/1000,'unixepoch','localtime'), key_remote_jid, data "
        "FROM message ORDER BY timestamp DESC LIMIT 10"
    ):
        print("  ", " | ".join(str(c) for c in linha))


# ---------------------------------------------------------------- restaurar
def restaurar(backup: Path, tipo: int):
    destino = DIRS_BACKUP[0]
    adb(["shell", "am", "force-stop", "com.whatsapp"])
    uid = adb(["shell", f"stat -c %u {destino}"]).strip()
    remoto = f"/sdcard/msgstore_restore.db.crypt{tipo}"
    adb(["push", str(backup), remoto])

    script = (
        f"mkdir -p {destino}/_antigos; "
        f"mv {destino}/msgstore*.crypt* {destino}/_antigos/ 2>/dev/null; "
        f"cp {remoto} {destino}/msgstore.db.crypt{tipo}; "
        f"chown {uid}:{uid} {destino}/msgstore.db.crypt{tipo}; "
        f"chmod 660 {destino}/msgstore.db.crypt{tipo}; "
        f"rm -f {remoto}"
    )
    try:
        adb(["shell", f"su -c '{script}'"])
    except RuntimeError as e:
        sys.exit(f"Falha ao copiar (precisa de root): {e}")
    adb(["shell", "am", "start", "-n", "com.whatsapp/com.whatsapp.Main"])
    print("Backup posicionado. Abra o WhatsApp e toque em RESTAURAR.")
    print("Os backups antigos ficaram em _antigos/.")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("listar"); p.add_argument("--pasta")
    p = sub.add_parser("chave"); p.add_argument("--destino", default="./key")
    p = sub.add_parser("descriptografar")
    p.add_argument("--backup", required=True); p.add_argument("--chave", default="./key")
    p.add_argument("--saida", default="msgstore.db")
    p = sub.add_parser("verificar"); p.add_argument("--db", required=True)
    p = sub.add_parser("restaurar"); p.add_argument("--backup", required=True)

    a = ap.parse_args()
    if a.cmd == "listar":
        listar(a.pasta)
    elif a.cmd == "chave":
        extrair_chave(Path(a.destino))
    elif a.cmd == "descriptografar":
        descriptografar(Path(a.backup), Path(a.chave), Path(a.saida))
    elif a.cmd == "verificar":
        verificar(Path(a.db))
    elif a.cmd == "restaurar":
        tipo = int(re.search(r"\.crypt(\d+)$", a.backup).group(1))
        restaurar(Path(a.backup), tipo)


if __name__ == "__main__":
    main()
