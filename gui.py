#!/usr/bin/env python3
"""Compara e organiza backups WhatsApp em Databases/Backups sem descriptografar."""
from __future__ import annotations
import glob
import os
import shutil
import threading
import zipfile
from datetime import datetime
from pathlib import Path
import customtkinter as ctk
from tkinter import filedialog, messagebox

CATEGORIES = {
    "msgstore": "msgstore (histórico principal)",
    "backup_settings": "backup_settings.json",
    "chatsettingsbackup": "chatsettingsbackup.db",
    "commerce_backup": "commerce_backup.db",
    "offloaded-media": "offloaded-media.db",
    "smb_backup": "smb_backup.db",
    "status_backup": "status_backup.db",
    "stickers_db": "stickers_db.bak",
    "wa": "wa.db",
    "avatar-password": "avatar-password.bkup",
    "other": "Outros arquivos",
}


def classify(path: Path) -> str:
    name = path.name.lower()
    # Retira o sufixo de extensão de criptografia para classificar também .crypt15.
    stem = name
    for suffix in (".crypt14", ".crypt15", ".crypt12"):
        if stem.endswith(suffix):
            stem = stem[:-len(suffix)]
            break
    stem = stem.replace(" - copiar", "").replace(" - copy", "").strip()
    if stem.startswith("msgstore"):
        return "msgstore"
    for category in CATEGORIES:
        if category in ("msgstore", "other", "wa"):
            continue
        if stem.startswith(category):
            return category
    if stem.startswith("wa.db"):
        return "wa"
    return "other"


def whatsapp_root_from_selected_directory(directory: Path) -> Path | None:
    """Convert a selected Databases/Backups/WhatsApp/root folder to the WhatsApp root."""
    p = directory
    lower = p.name.lower()
    if lower in ("databases", "backups"):
        return p.parent
    if lower == "whatsapp":
        return p
    # Resolve common roots: phone storage, Android, media, com.whatsapp, or WhatsApp.
    candidates = [
        p / "Android/media/com.whatsapp/WhatsApp",
        p / "media/com.whatsapp/WhatsApp",
        p / "com.whatsapp/WhatsApp",
        p / "WhatsApp",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    parts=[part.lower() for part in p.parts]
    if parts[-3:]==["android","media","com.whatsapp"]:
        return p/"WhatsApp"
    if parts[-2:]==["android","media"]:
        return p/"com.whatsapp/WhatsApp"
    if parts[-1:]==["android"]:
        return p/"media/com.whatsapp/WhatsApp"
    return p/"Android/media/com.whatsapp/WhatsApp"


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Comparador e organizador de backups WhatsApp")
        self.geometry("1040x800")
        self.minsize(800, 650)
        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.tabs = ctk.CTkTabview(self)
        self.tabs.grid(row=0, column=0, padx=14, pady=14, sticky="nsew")
        self.main_tab = self.tabs.add("Comparar e restaurar")
        self.filter_tab = self.tabs.add("Filtros e ordem")
        self.main_tab.grid_columnconfigure(0, weight=1)
        self.main_tab.grid_rowconfigure(4, weight=1)
        self.filter_tab.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkLabel(self.main_tab, text="Comparar e organizar backups do WhatsApp", font=ctk.CTkFont(size=21, weight="bold")).grid(row=0,column=0,padx=12,pady=(10,6),sticky="w")
        self.inputs = self._path_row(self.main_tab,1,"Arquivos de backup","Selecione arquivos ou digite curingas, ex.: /backup/*.crypt14","files")
        self.root_entry = self._path_row(self.main_tab,2,"Pasta de destino","Escolha qualquer pasta; serão criadas Databases e Backups","root")
        self.compare_btn = ctk.CTkButton(self.main_tab,text="Comparar arquivos",command=self.compare)
        self.compare_btn.grid(row=3,column=0,padx=12,pady=6,sticky="w")
        self.results = ctk.CTkTextbox(self.main_tab)
        self.results.grid(row=4,column=0,padx=12,pady=6,sticky="nsew")
        self.restore_btn=ctk.CTkButton(self.main_tab,text="Organizar/restaurar arquivos filtrados",command=self.restore_filtered)
        self.restore_btn.grid(row=5,column=0,padx=12,pady=8,sticky="w")

        ctk.CTkLabel(self.filter_tab,text="Selecione os tipos de arquivo incluídos",font=ctk.CTkFont(size=20,weight="bold")).grid(row=0,column=0,columnspan=2,padx=12,pady=(12,8),sticky="w")
        self.filter_vars={}
        for index,(key,label) in enumerate(CATEGORIES.items()):
            var=ctk.BooleanVar(value=True)
            self.filter_vars[key]=var
            cb=ctk.CTkCheckBox(self.filter_tab,text=label,variable=var,command=self.compare)
            cb.grid(row=1+index//2,column=index%2,padx=14,pady=8,sticky="w")
        sort_frame=ctk.CTkFrame(self.filter_tab)
        sort_frame.grid(row=7,column=0,columnspan=2,padx=12,pady=12,sticky="ew")
        ctk.CTkLabel(sort_frame,text="Ordenar por").pack(side="left",padx=(10,6),pady=10)
        self.sort_box=ctk.CTkComboBox(sort_frame,values=["Data — crescente (antigo primeiro)","Data — decrescente (recente primeiro)","Tamanho — crescente (menor primeiro)","Tamanho — decrescente (maior primeiro)"],command=lambda _v:self.compare(),width=350)
        self.sort_box.set("Data — crescente (antigo primeiro)")
        self.sort_box.pack(side="left",padx=8,pady=8)
        self.status=ctk.CTkLabel(self,text="Pronto")
        self.status.grid(row=1,column=0,padx=18,pady=(0,8),sticky="w")
        self.files=[]
        self.filtered=[]
        self._set_busy(False)
        self._log("Classificação: msgstore* → Databases; demais arquivos → Backups, dentro da pasta de destino escolhida.")
        self._log("Os arquivos são copiados sem descriptografia. Antes de copiar, o app cria um ZIP de segurança da pasta de destino existente.")
        self.compare()

    def _path_row(self,parent,row,label,placeholder,kind):
        frame=ctk.CTkFrame(parent); frame.grid(row=row,column=0,padx=12,pady=5,sticky="ew"); frame.grid_columnconfigure(1,weight=1)
        ctk.CTkLabel(frame,text=label,width=190,anchor="w").grid(row=0,column=0,padx=8,pady=8)
        entry=ctk.CTkEntry(frame,placeholder_text=placeholder); entry.grid(row=0,column=1,padx=8,pady=8,sticky="ew")
        if kind=="files":
            button=ctk.CTkButton(frame,text="Selecionar arquivos",width=135,command=lambda:self._pick_files(entry))
        else:
            button=ctk.CTkButton(frame,text="Escolher pasta",width=135,command=lambda:self._pick_root(entry))
        button.grid(row=0,column=2,padx=8,pady=8)
        return entry

    def _pick_files(self,entry):
        paths=filedialog.askopenfilenames(title="Selecione backups de qualquer tipo",filetypes=[("Todos os arquivos","*"),("Arquivos crypt","*.crypt*")])
        if paths:
            entry.delete(0,"end"); entry.insert(0,";".join(paths))
            self.compare()

    def _pick_root(self,entry):
        chosen=filedialog.askdirectory(title="Selecione qualquer pasta para organizar os arquivos")
        if not chosen: return
        entry.delete(0,"end"); entry.insert(0,chosen)
        self._log(f"Pasta de destino definida como: {chosen}")

    def _collect_files(self):
        found=[]
        for raw in (part.strip() for part in self.inputs.get().split(";")):
            if not raw: continue
            pattern=os.path.expanduser(raw)
            try: matches=glob.glob(pattern,include_hidden=True)
            except TypeError: matches=glob.glob(pattern)
            if not matches and Path(pattern).is_file(): matches=[pattern]
            found.extend(Path(p).resolve() for p in matches if Path(p).is_file())
        return sorted(set(found))

    def _sort_key(self):
        value=self.sort_box.get()
        if value.startswith("Tamanho"):
            return (lambda p:p.stat().st_size, "dec" if "decrescente" in value else "asc")
        return (lambda p:p.stat().st_mtime, "dec" if "decrescente" in value else "asc")

    @staticmethod
    def _human_size(size):
        n=float(size)
        for unit in ("B","KB","MB","GB","TB"):
            if n<1024 or unit=="TB": return f"{n:.1f} {unit}"
            n/=1024

    def compare(self,*_):
        self.files=self._collect_files()
        enabled={key for key,var in self.filter_vars.items() if var.get()}
        self.filtered=[p for p in self.files if classify(p) in enabled]
        key,direction=self._sort_key()
        self.filtered.sort(key=key,reverse=(direction=="dec"))
        self.results.delete("1.0","end")
        if not self.files:
            self.results.insert("end","Nenhum arquivo encontrado. Selecione arquivos ou informe um padrão.")
            return
        self.results.insert("end",f"Encontrados: {len(self.files)} | após filtros: {len(self.filtered)} | selecionados: {sum(p.stat().st_size for p in self.filtered):,} bytes\n")
        self.results.insert("end",f"Ordem: {self.sort_box.get()}\n\n")
        for p in self.filtered:
            stamp=datetime.fromtimestamp(p.stat().st_mtime).astimezone().strftime("%Y-%m-%d %H:%M:%S")
            dest="Databases" if classify(p)=="msgstore" else "Backups"
            self.results.insert("end",f"[{dest}] {p.name} | {self._human_size(p.stat().st_size)} | {stamp}\n{p}\n")
        msgstores=[p for p in self.filtered if classify(p)=="msgstore"]
        if not msgstores:
            self.results.insert("end","\nAVISO: nenhum msgstore selecionado. Os arquivos auxiliares não restauram o histórico principal de conversas.\n")
        self.results.insert("end","\nO arquivo mais antigo e o maior, conforme filtros atuais:\n")
        if self.filtered:
            oldest=min(self.filtered,key=lambda p:p.stat().st_mtime)
            largest=max(self.filtered,key=lambda p:p.stat().st_size)
            self.results.insert("end",f"Mais antigo: {oldest.name} ({self._human_size(oldest.stat().st_size)})\nMaior: {largest.name} ({self._human_size(largest.stat().st_size)})\n")

    def _set_busy(self,busy):
        state="disabled" if busy else "normal"
        self.compare_btn.configure(state=state); self.restore_btn.configure(state=state)
        self.status.configure(text="Organizando arquivos..." if busy else "Pronto")

    def _log(self,text): self.after(0,lambda:(self.status.configure(text=text[:100]),None))

    def restore_filtered(self):
        # Refresh according to the current checkboxes and sort selection.
        self.compare()
        if not self.filtered:
            messagebox.showerror("Sem arquivos","Não há arquivos selecionados pelos filtros atuais."); return
        raw=self.root_entry.get().strip()
        if not raw:
            messagebox.showerror("Destino necessário","Escolha a pasta onde serão criadas Databases e Backups."); return
        root=Path(os.path.expanduser(raw))
        sources=list(self.filtered)
        routes={"Databases":sum(classify(p)=="msgstore" for p in sources),"Backups":sum(classify(p)!="msgstore" for p in sources)}
        replacements=[p for p in sources if (root/("Databases" if classify(p)=="msgstore" else "Backups")/p.name).exists()]
        msgstores=routes["Databases"]
        msg_warning="\n\nNão há msgstore selecionado; os arquivos auxiliares não recuperam o histórico principal de conversas." if not msgstores else ""
        overwrite="\nArquivos homônimos serão substituídos após o ZIP de segurança.\n" if replacements else ""
        preview="\n".join(f"{p.name} → {'Databases' if classify(p)=='msgstore' else 'Backups'} ({self._human_size(p.stat().st_size)})" for p in sources[:20])
        if len(sources)>20: preview+=f"\n… mais {len(sources)-20} arquivo(s)"
        ok=messagebox.askyesno("Confirmar organização",f"Pasta de destino: {root}\n\n{preview}\n\n{overwrite}Serão criadas as subpastas Databases e Backups. Será criado um ZIP de segurança da pasta de destino antes de copiar. Não haverá descriptografia.{msg_warning}\n\nContinuar?")
        if not ok: return
        self._set_busy(True)
        threading.Thread(target=self._restore_worker,args=(sources,root),daemon=True).start()

    def _restore_worker(self,sources,root):
        try:
            stamp=datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
            archive=root.parent/f"{root.name}_before_organize_{stamp}.zip"
            archive.parent.mkdir(parents=True,exist_ok=True)
            with zipfile.ZipFile(archive,"w",compression=zipfile.ZIP_DEFLATED,allowZip64=True) as zf:
                if root.exists():
                    for item in root.rglob("*"):
                        if item.is_file() and not item.is_symlink(): zf.write(item,item.relative_to(root))
            copied=0
            for source in sources:
                folder="Databases" if classify(source)=="msgstore" else "Backups"
                target=root/folder
                target.mkdir(parents=True,exist_ok=True)
                destination=target/source.name
                if source.resolve()==destination.resolve():
                    self._log(f"Já está no destino; ignorado: {destination}")
                    continue
                shutil.copy2(source,destination)
                copied+=1
                self._log(f"Copiado para {folder}: {source.name}")
            self.after(0,lambda:messagebox.showinfo("Organização concluída",f"Arquivos copiados: {copied}\nDatabases: {routes_count(sources,'msgstore')} arquivo(s)\nBackups: {routes_count(sources,'other')} arquivo(s)\n\nPasta: {root}\nZIP de segurança: {archive}"))
        except Exception as exc:
            error=str(exc)
            self.after(0,lambda msg=error:messagebox.showerror("Falha ao copiar",msg))
        finally:
            self.after(0,lambda:self._set_busy(False))


def routes_count(paths,mode):
    return sum(1 for p in paths if (classify(p)=="msgstore") == (mode=="msgstore"))


if __name__=="__main__":
    App().mainloop()
