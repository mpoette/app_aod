"""Chargement et mise en forme des données du projet « LLM et AOD ».

Sources (dossier Data/) :
- Métadonnées.xlsx            : 1 ligne par « Cas clinique N » (Sexe, ATC, Délais, …, Xie = spécialité)
- <Spécialité>/*.REDCap.xml   : dictionnaire REDCap (texte des vignettes, questions, listes de choix)
- <Spécialité>/*_DATA_*.csv   : export REDCap des réponses (1 ligne par répondant)

Le n-ième cas d'un projet REDCap (cas_n / *_vn) correspond au n-ième « Cas clinique »
de la même spécialité dans Métadonnées.xlsx (ordre des lignes).
"""
from __future__ import annotations

import difflib
import hashlib
import html
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

NS = {"o": "http://www.cdisc.org/ns/odm/v1.3", "r": "https://projectredcap.org"}
R = "{https://projectredcap.org}"

# Questions dans l'ordre d'affichage : (préfixe REDCap, libellé court)
QUESTIONS: list[tuple[str, str]] = [
    ("risque_hem", "Risque hémorragique"),
    ("risque_thromb", "Risque thrombotique"),
    ("interruption", "Interruption"),
    ("dern_prise", "Dernière prise"),
    ("relais", "Relais héparine"),
    ("relais_debut", "Début du relais"),
    ("reversion", "Réversion"),
    ("dosage", "Dosage biologique"),
]
QUESTION_LABEL = dict(QUESTIONS)
STRATEGIE = "Stratégie globale"

# Rôles harmonisés entre spécialités (les libellés REDCap mentionnent la spécialité)
ROLE_LABELS = {
    "interne_ar": "Interne AR",
    "mar_vasculaire": "MAR spécialisé",
    "mar_autre": "MAR non spécialisé",
    "expert_clot": "Expert CLOT",
    "expert_gihp": "Expert GIHP",
}

META_COLUMNS = ["Spécialité", "Sexe", "ATC", "Délais", "Indication", "Fonction rénale", "ALR"]


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", s.lower())


def _clean_html(raw: str) -> str:
    """HTML REDCap → HTML simple et sûr (p, br, ul, li, strong, em)."""
    if not raw:
        return ""
    s = html.unescape(raw)
    s = re.sub(r"</?(div|span)[^>]*>", "", s)
    s = re.sub(r"<(/?)(p|ul|ol|li|strong|b|em|i)\b[^>]*>", r"<\1\2>", s)
    s = re.sub(r"<br[^>]*>", "<br>", s)
    s = re.sub(r"<(?!/?(p|ul|ol|li|strong|b|em|i|br)\b)[^>]+>", "", s)
    return s.strip()


@dataclass
class Item:
    name: str
    question: str
    field_type: str
    choices: dict[str, str] = field(default_factory=dict)
    note: str = ""


@dataclass
class Projet:
    specialite: str          # nom du dossier (ex. « Vasculaire »)
    titre: str
    vignettes: dict[int, dict]   # n local -> {"text":…, "html":…}
    items: dict[str, Item]
    reponses: pd.DataFrame       # export brut


def parse_redcap_xml(path: Path) -> tuple[str, dict[int, dict], dict[str, Item]]:
    root = ET.parse(path).getroot()
    mdv = root.find(".//o:MetaDataVersion", NS)
    titre = root.findtext(".//o:StudyName", default=path.stem, namespaces=NS)
    codelists = {
        cl.get("OID"): {
            it.get("CodedValue"): it.findtext(".//o:TranslatedText", default="", namespaces=NS)
            for it in cl.findall("o:CodeListItem", NS)
        }
        for cl in mdv.findall("o:CodeList", NS)
    }
    items: dict[str, Item] = {}
    vignettes: dict[int, dict] = {}
    for it in mdv.findall("o:ItemDef", NS):
        name = it.get("Name")
        q = it.find("o:Question", NS)
        text = q.findtext("o:TranslatedText", default="", namespaces=NS) if q is not None else ""
        fmt = q.findtext("r:FormattedTranslatedText", default="", namespaces=NS) if q is not None else ""
        cl = it.find("o:CodeListRef", NS)
        items[name] = Item(
            name=name,
            question=html.unescape(text or ""),
            field_type=it.get(R + "FieldType", ""),
            choices=codelists.get(cl.get("CodeListOID"), {}) if cl is not None else {},
            note=it.get(R + "FieldNote", ""),
        )
        m = re.fullmatch(r"vignette_v(\d+)", name)
        if m:
            vignettes[int(m.group(1))] = {
                "text": (text or "").strip(),
                "html": _clean_html(fmt) or html.escape(text or "").replace("\n", "<br>"),
            }
    return titre, vignettes, items


def html_to_text(raw_html: str) -> str:
    """HTML nettoyé d'une vignette → texte brut lisible (listes à puces conservées), pour les prompts."""
    s = re.sub(r"\s*<li>\s*", "\n- ", raw_html)
    s = re.sub(r"\s*</(p|ul|ol)>\s*", "\n", s)
    s = re.sub(r"<br>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    s = re.sub(r"\n{2,}", "\n", s)
    return s.strip()


def data_fingerprint(data_dir: Path) -> str:
    """Empreinte du contenu de Data/ : identifie la version des données utilisée par un benchmark."""
    h = hashlib.sha256()
    for p in sorted(data_dir.rglob("*")):
        if p.is_file() and not p.name.startswith("."):
            h.update(str(p.relative_to(data_dir)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def load_preprompt(data_dir: Path) -> str | None:
    p = data_dir / "preprompt.txt"
    return p.read_text(encoding="utf-8").strip() if p.exists() else None


def _latest(files: list[Path]) -> Path | None:
    return sorted(files, key=lambda p: p.name)[-1] if files else None


def load_projets(data_dir: Path) -> list[Projet]:
    projets = []
    for d in sorted(p for p in data_dir.iterdir() if p.is_dir()):
        xml = _latest(list(d.glob("*.xml")))
        csv = _latest([p for p in d.glob("*.csv") if "_DATA_" in p.name] or list(d.glob("*.csv")))
        if xml is None:
            continue
        titre, vignettes, items = parse_redcap_xml(xml)
        rep = pd.read_csv(csv, sep=None, engine="python", dtype=str) if csv else pd.DataFrame()
        projets.append(Projet(d.name, titre, vignettes, items, rep))
    return projets


def load_metadata(data_dir: Path) -> pd.DataFrame:
    path = next(data_dir.glob("*tadonn*.xlsx"))
    df = pd.read_excel(path, sheet_name=0)
    df = df.rename(columns={df.columns[0]: "Cas", "Xie": "Spécialité"})
    df = df[df["Cas"].astype(str).str.contains(r"\d", na=False)].copy()
    df["num"] = df["Cas"].str.extract(r"(\d+)").astype(int)
    for c in META_COLUMNS:
        if c in df:
            df[c] = df[c].astype(str).str.strip()
    # coquilles éventuelles (ex. « Neurochirugie »)
    df["Spécialité"] = df["Spécialité"].replace({"Neurochirugie": "Neurochirurgie"})
    df["rang"] = df.groupby("Spécialité").cumcount() + 1
    return df.sort_values("num").reset_index(drop=True)


# --------------------------------------------------------------------------- décodage

def _decode(prefix: str, raw, items: dict[str, Item], n: int):
    if raw is None or (isinstance(raw, float) and pd.isna(raw)) or str(raw).strip() == "":
        return None
    raw = str(raw).strip()
    if prefix in ("interruption", "reversion", "dosage"):
        return {"1": "Oui", "0": "Non"}.get(raw, raw)
    if prefix in ("dern_prise", "relais_debut"):
        try:
            v = int(float(raw))
        except ValueError:
            return raw
        return "J0" if v == 0 else f"J-{v}"
    if prefix == "relais":
        return {"0": "Non", "1": "HBPM", "2": "HNF"}.get(raw, raw)
    if prefix == "confiance":
        try:
            return int(float(raw))
        except ValueError:
            return None
    it = items.get(f"{prefix}_v{n}")
    return it.choices.get(raw, raw) if it else raw


def _strategie(r: dict) -> str | None:
    if r.get("reversion") is not None and r.get("interruption") is None:
        return "Réversion" if r["reversion"] == "Oui" else "Pas de réversion"
    if r.get("interruption") is None:
        return None
    if r["interruption"] == "Non":
        return "Poursuite"
    s = f"Arrêt {r.get('dern_prise') or '?'}"
    if r.get("relais") in ("HBPM", "HNF"):
        s += f" + {r['relais']} {r.get('relais_debut') or '?'}"
    else:
        s += ", sans relais"
    return s


@dataclass
class Dataset:
    cases: pd.DataFrame       # 1 ligne par cas (métadonnées + texte)
    answers: pd.DataFrame     # format long : case_id, respondant, role, question, valeur
    choices_order: dict[str, list[str]]  # ordre des modalités par question


def build_dataset(data_dir: Path) -> Dataset:
    meta = load_metadata(data_dir)
    projets = load_projets(data_dir)
    specs = meta["Spécialité"].unique().tolist()

    rows_case, rows_ans = [], []
    order: dict[str, list[str]] = {}
    # case_id -> (projet, n local).  Un projet REDCap peut couvrir plusieurs spécialités
    # (ex. « Chir. Viscérale » = 8 cas d'urologie + 10 de viscérale) : on cherche la fenêtre
    # contiguë de Métadonnées.xlsx contenant le bloc de la spécialité et dont le sexe des
    # patients concorde au mieux avec le texte des vignettes.
    mapping: dict[int, tuple[Projet, int]] = {}
    nums = meta["num"].tolist()
    norm_specs = [_norm(s) for s in specs]
    for p in projets:
        match = difflib.get_close_matches(_norm(p.specialite), norm_specs, n=1, cutoff=0.6)
        if not match:
            continue
        spec = specs[norm_specs.index(match[0])]
        idx = meta.index[meta["Spécialité"] == spec].tolist()
        n_vig = max(p.vignettes) if p.vignettes else len(idx)
        lo, hi = idx[0], idx[-1]
        starts = [s for s in range(max(0, hi - n_vig + 1), lo + 1) if s + n_vig <= len(meta)] or [lo]

        def score(s):
            ok = 0
            for k in range(1, n_vig + 1):
                txt = p.vignettes.get(k, {}).get("text", "")
                ok += txt.split(" ", 1)[0] == meta.loc[s + k - 1, "Sexe"] if txt else 0
            return (ok, s == lo)
        start = max(starts, key=score)
        for k in range(1, n_vig + 1):
            mapping.setdefault(int(nums[start + k - 1]), (p, k))

    for _, m in meta.iterrows():
        p, n = mapping.get(int(m["num"]), (None, int(m["rang"])))
        v = p.vignettes.get(n) if p else None
        rows_case.append({
            "case_id": int(m["num"]),
            "Cas": f"Cas {int(m['num'])}",
            "n_local": n,
            **{c: m.get(c) for c in META_COLUMNS},
            "Urgence immédiate": bool(p and f"reversion_v{n}" in p.items),
            "texte": v["text"] if v else "",
            "texte_prompt": html_to_text(v["html"]) if v else "",
            "html": v["html"] if v else "",
            "projet": p.titre if p else "",
        })
        if not p or p.reponses.empty:
            continue
        for item_prefix, _ in QUESTIONS:
            it = p.items.get(f"{item_prefix}_v{n}")
            if it and item_prefix in ("risque_hem", "risque_thromb"):
                order.setdefault(item_prefix, list(it.choices.values()))
        complete_col = f"cas_{n}_complete"
        for _, r in p.reponses.iterrows():
            if complete_col in r and str(r[complete_col]) != "2":
                continue  # cas non complété par ce répondant
            dec = {q: _decode(q, r.get(f"{q}_v{n}"), p.items, n) for q, _ in QUESTIONS + [("confiance", "")]}
            if all(v is None for k, v in dec.items() if k != "confiance"):
                continue
            role = str(r.get("role", ""))
            base = {
                "case_id": int(m["num"]),
                "respondant": f"{p.specialite[:4]}-{r.get('record_id')}",
                "role": ROLE_LABELS.get(role, role),
                "role_code": role,
                "experience": r.get("mar_vasc_exp"),
                "confiance": dec["confiance"],
                "source": "Humain",
            }
            for q, lab in QUESTIONS:
                if dec[q] is not None:
                    rows_ans.append({**base, "question": lab, "valeur": dec[q]})
            strat = _strategie(dec)
            if strat:
                rows_ans.append({**base, "question": STRATEGIE, "valeur": strat})

    order.update({
        "Risque hémorragique": order.pop("risque_hem", ["Faible", "Élevé", "Très élevé", "Je ne sais pas"]),
        "Risque thrombotique": order.pop("risque_thromb", ["Faible", "Élevé", "Je ne sais pas"]),
        "Interruption": ["Oui", "Non"],
        "Réversion": ["Oui", "Non"],
        "Dosage biologique": ["Oui", "Non"],
        "Relais héparine": ["Non", "HBPM", "HNF"],
    })
    answers = pd.DataFrame(rows_ans, columns=[
        "case_id", "respondant", "role", "role_code", "experience", "confiance", "source", "question", "valeur"])
    return Dataset(pd.DataFrame(rows_case), answers, order)


# --------------------------------------------------------------------------- statistiques

def sort_values(question: str, values, order: dict[str, list[str]]) -> list:
    vals = list(dict.fromkeys(values))
    ref = order.get(question)
    if ref:
        return sorted(vals, key=lambda v: (ref.index(v) if v in ref else len(ref), str(v)))

    def key(v):
        m = re.match(r"J-?(\d+)", str(v))
        return (0, int(m.group(1))) if m and str(v).startswith("J") and "+" not in str(v) else (1, str(v))
    return sorted(vals, key=key)


def summarize(answers: pd.DataFrame) -> pd.DataFrame:
    """Par cas × question : réponse majoritaire, accord (% de la modalité majoritaire), n, distribution."""
    out = []
    for (cid, q), g in answers.groupby(["case_id", "question"], sort=False):
        cnt = Counter(g["valeur"])
        n = sum(cnt.values())
        top = cnt.most_common()
        best = top[0][1]
        majo = [v for v, c in top if c == best]
        out.append({
            "case_id": cid,
            "question": q,
            "majoritaire": " / ".join(map(str, majo)) + (" (égalité)" if len(majo) > 1 else ""),
            "majoritaires": majo,
            "accord": best / n,
            "n": n,
            "n_modalites": len(cnt),
            "distribution": dict(cnt),
        })
    return pd.DataFrame(out, columns=["case_id", "question", "majoritaire", "majoritaires", "accord", "n", "n_modalites", "distribution"])
