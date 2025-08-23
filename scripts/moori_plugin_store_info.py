#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import argparse
import pathlib
import mimetypes
import re
from typing import Dict, Any, List, Optional, Tuple
from uuid import uuid4

import yaml
import requests
from markdown import markdown

# ------------------------
# Helpers
# ------------------------

def abort(msg: str, code: int = 1):
    print(f"::error::{msg}")
    sys.exit(code)

def load_yaml(path: pathlib.Path) -> Dict[str, Any]:
    if not path.exists():
        abort(f"YAML-Datei nicht gefunden: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if "store" not in data:
        abort("Top-Level key 'store' fehlt in der YAML.")
    return data["store"]

def read_file(value: Any, base_dir: pathlib.Path) -> str:
    """
    Unterstützt beide Formen:
    - dict: {"file": "docs/store.md"}
    - string: "file:docs/store.md"
    """
    # Form 1: dict mit "file"
    if isinstance(value, dict) and "file" in value:
        p = (base_dir / str(value["file"])).resolve()
        if not p.exists():
            abort(f"Datei in YAML referenziert, aber nicht vorhanden: {p}")
        return p.read_text(encoding="utf-8")

    # Form 2: string der mit "file:" beginnt
    if isinstance(value, str):
        m = re.match(r"^\s*file\s*:\s*(.+?)\s*$", value)
        if m:
            rel = m.group(1)
            p = (base_dir / rel).resolve()
            if not p.exists():
                abort(f"Datei in YAML referenziert, aber nicht vorhanden: {p}")
            return p.read_text(encoding="utf-8")
        # sonst: echter Textstring
        return value

    return ""

def md_to_html(md_text: str) -> str:
    # Markdown -> HTML
    return markdown(md_text, extensions=["extra", "sane_lists", "toc"])

def build_locale_html(
    locale_key: str,
    description_map: Dict[str, Any],
    manual_map: Dict[str, Any],
    highlights_map: Dict[str, List[str]],
    features_map: Dict[str, List[str]],
    base_dir: pathlib.Path
) -> str:
    desc_md = read_file(description_map.get(locale_key, ""), base_dir)
    manual_md = read_file(manual_map.get(locale_key, ""), base_dir)

    highlights = highlights_map.get(locale_key, []) or []
    features = features_map.get(locale_key, []) or []

    # Fallbacks, falls Locale fehlt
    if not desc_md and "en" in description_map:
        desc_md = read_file(description_map["en"], base_dir)
    if not manual_md and "en" in manual_map:
        manual_md = read_file(manual_map["en"], base_dir)

    desc_html = md_to_html(desc_md) if desc_md else ""
    manual_html = md_to_html(manual_md) if manual_md else ""

    def list_block(items: List[str]) -> str:
        if not items:
            return ""
        lis = "".join(f"<li>{i}</li>" for i in items)
        return f"<ul>{lis}</ul>"

    html = []
    if desc_html:
        html += [f"<section id='description'>{desc_html}</section>"]
    if manual_html:
        html += [f"<section id='installation-manual'><h2>Installation</h2>{manual_html}</section>"]

    if highlights:
        html += [f"<section id='highlights'><h2>Highlights</h2>{list_block(highlights)}</section>"]
    if features:
        html += [f"<section id='features'><h2>Features</h2>{list_block(features)}</section>"]

    final = "\n".join(html)
    wrapper = f"""<!doctype html>
<html lang="{ 'de' if locale_key.startswith('de') else 'en' }">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<body>
{final}
</body>
</html>"""
    return wrapper

# ------------------------
# Shopware API Client
# ------------------------

class ShopwareClient:
    def __init__(self, base_url: str, client_id: str, client_secret: str):
        self.base = base_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self._token = None
        self.session = requests.Session()

    def token(self) -> str:
        if self._token:
            return self._token
        url = f"{self.base}/api/oauth/token"
        resp = self.session.post(url, json={
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret
        }, timeout=30)
        if resp.status_code != 200:
            abort(f"OAuth Token fehlgeschlagen ({resp.status_code}): {resp.text}")
        self._token = resp.json()["access_token"]
        return self._token

    def headers(self, sw_language_id: Optional[str] = None) -> Dict[str, str]:
        h = {
            "Authorization": f"Bearer {self.token()}",
            "Content-Type": "application/json"
        }
        # Für Übersetzungen pro Sprache
        if sw_language_id:
            h["sw-language-id"] = sw_language_id
        return h

    def search_language_id(self, locale_code: str) -> Optional[str]:
        url = f"{self.base}/api/search/language"
        body = {"filter": [{"type": "equals", "field": "locale.code", "value": locale_code}]}
        r = self.session.post(url, headers=self.headers(), json=body, timeout=30)
        if r.status_code != 200:
            abort(f"Language-Suche fehlgeschlagen: {r.text}")
        data = r.json()
        elements = data.get("data", [])
        return elements[0]["id"] if elements else None

    def find_product_id_by_number(self, product_number: str) -> Optional[str]:
        url = f"{self.base}/api/search/product"
        body = {"filter": [{"type": "equals", "field": "productNumber", "value": product_number}]}
        r = self.session.post(url, headers=self.headers(), json=body, timeout=30)
        if r.status_code != 200:
            abort(f"Produkt-Suche fehlgeschlagen: {r.text}")
        data = r.json()
        items = data.get("data", [])
        return items[0]["id"] if items else None

    def patch_product_description(self, product_id: str, html: str, sw_language_id: Optional[str] = None):
        url = f"{self.base}/api/product/{product_id}"
        payload = {
            "description": html
            # Optional: weitere Felder wie "metaDescription", "customFields" etc.
        }
        r = self.session.patch(url, headers=self.headers(sw_language_id), json=payload, timeout=60)
        if r.status_code not in (200, 204):
            abort(f"Produkt-Update fehlgeschlagen ({r.status_code}): {r.text}")

    def search_media_by_name(self, file_stem: str, ext: str) -> str | None:
        """Sucht vorhandene Media-Entity per fileName + fileExtension und gibt deren ID zurück."""
        url = f"{self.base}/api/search/media"
        body = {
            "filter": [
                {"type": "equals", "field": "fileName", "value": file_stem},
                {"type": "equals", "field": "fileExtension", "value": ext.lower()},
            ],
            "limit": 1,
        }
        r = self.session.post(url, headers=self.headers(), json=body, timeout=30)
        if r.status_code != 200:
            abort(f"Media-Suche fehlgeschlagen: {r.status_code} {r.text}")
        data = r.json().get("data", [])
        return data[0]["id"] if data else None

    def upload_media(self, file_path: pathlib.Path, repo_name: str) -> str:
        """
        1) Falls bereits ein Medium mit gleichem fileName+Extension existiert -> Datei ERSETZEN.
        2) Sonst neues Media anlegen und Datei hochladen.
        Gibt die mediaId zurück.
        """

        # Basename + Repo-Name kombinieren
        base_stem = file_path.stem
        ext = (file_path.suffix.lstrip(".") or "png").lower()
        file_stem = f"{repo_name}__{base_stem}"

        mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        size = file_path.stat().st_size
        if size == 0:
            abort(f"Datei ist leer: {file_path}")

        # A) Bestehendes Medium finden
        existing_id = self.search_media_by_name(file_stem, ext)
        if existing_id:
            upload_url = f"{self.base}/api/_action/media/{existing_id}/upload?extension={ext}&fileName={file_stem}"
            with file_path.open("rb") as f:
                r = self.session.post(
                    upload_url,
                    headers={
                        "Authorization": f"Bearer {self.token()}",
                        "Content-Type": mime,
                        "Content-Length": str(size),
                    },
                    data=f,  # roher Stream verhindert Content-Length-Mismatch
                    timeout=180,
                )
            if r.status_code not in (200, 204):
                abort(f"Media-Upload (replace) fehlgeschlagen: {r.status_code} {r.text}")
            return existing_id

        # B) Neu anlegen + hochladen
        media_id = uuid4().hex  # 32-hex ohne Bindestriche
        create_url = f"{self.base}/api/media?_response=true"
        r1 = self.session.post(create_url, headers=self.headers(), json={"id": media_id}, timeout=30)
        if r1.status_code not in (200, 201):
            abort(f"Media-Erstellung fehlgeschlagen: {r1.status_code} {r1.text}")

        upload_url = f"{self.base}/api/_action/media/{media_id}/upload?extension={ext}&fileName={file_stem}"
        with file_path.open("rb") as f:
            r2 = self.session.post(
                upload_url,
                headers={
                    "Authorization": f"Bearer {self.token()}",
                    "Content-Type": mime,
                    "Content-Length": str(size),
                },
                data=f,
                timeout=180,
            )
        if r2.status_code not in (200, 204):
            # Falls hier trotz Neuanlage Duplicate aufschlägt (z. B. Race Condition), versuche Retry mit modifiziertem Dateinamen:
            if r2.status_code in (400, 409) and "CONTENT__MEDIA_DUPLICATED_FILE_NAME" in r2.text:
                alt_stem = f"{file_stem}-{media_id[:8]}"
                retry_url = f"{self.base}/api/_action/media/{media_id}/upload?extension={ext}&fileName={alt_stem}"
                with file_path.open("rb") as f:
                    r_retry = self.session.post(
                        retry_url,
                        headers={
                            "Authorization": f"Bearer {self.token()}",
                            "Content-Type": mime,
                            "Content-Length": str(size),
                        },
                        data=f,
                        timeout=180,
                    )
                if r_retry.status_code not in (200, 204):
                    abort(f"Media-Upload fehlgeschlagen (Retry): {r_retry.status_code} {r_retry.text}")
            else:
                abort(f"Media-Upload fehlgeschlagen: {r2.status_code} {r2.text}")

        return media_id

    def set_product_media(self, product_id: str, media_ids: List[Tuple[str, int]], cover_media_id: Optional[str]):
        """
        media_ids: Liste von (mediaId, position)
        cover_media_id: falls gesetzt -> Produkt-Cover
        """
        # Produkt lesen (um vorhandene Media-Relationen nicht zu duplizieren könnte man sie erst holen;
        # hier setzen wir vollständig neu.)
        url = f"{self.base}/api/product/{product_id}"
        media_payload = [{"mediaId": mid, "position": pos} for (mid, pos) in media_ids]

        payload = {"media": media_payload}
        if cover_media_id:
            payload["coverId"] = cover_media_id

        r = self.session.patch(url, headers=self.headers(), json=payload, timeout=60)
        if r.status_code not in (200, 204):
            abort(f"Produkt-Medien-Update fehlgeschlagen: {r.text}")

# ------------------------
# Main
# ------------------------

def main():
    parser = argparse.ArgumentParser(description="Sync store.yaml to Shopware product")
    parser.add_argument("--yaml-path", required=False, default="store.yaml")
    parser.add_argument("--repo-name", required=False, default=os.environ.get("REPO_NAME", ""))
    args = parser.parse_args()

    base_dir = pathlib.Path(".").resolve()
    yaml_path = base_dir / args.yaml_path

    store = load_yaml(yaml_path)

    repo_name = args.repo_name.strip()
    if not repo_name:
        abort("Repo-Name nicht gesetzt (REPO_NAME).")

    shop_url = os.environ.get("SHOPWARE_URL", "").strip()
    client_id = os.environ.get("SHOPWARE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("SHOPWARE_CLIENT_SECRET", "").strip()
    if not (shop_url and client_id and client_secret):
        abort("SHOPWARE_URL / SHOPWARE_CLIENT_ID / SHOPWARE_CLIENT_SECRET fehlen in den Secrets.")

    # 1) HTML je Locale bauen
    out_dir = base_dir / "dist"
    out_dir.mkdir(parents=True, exist_ok=True)

    description = store.get("description", {})
    manual = store.get("installation_manual", {})
    highlights = store.get("highlights", {})
    features = store.get("features", {})

    locales_detected = sorted(set(
        list(description.keys()) +
        list(manual.keys()) +
        list(highlights.keys()) +
        list(features.keys())
    ))

    # Map von Locale (de/en) -> full html
    html_per_locale: Dict[str, str] = {}
    for loc in locales_detected:
        html = build_locale_html(loc, description, manual, highlights, features, base_dir)
        html_per_locale[loc] = html
        # Datei schreiben (z. B. dist/store_de.html)
        suffix = "de" if loc.startswith("de") else "en"
        (out_dir / f"store_{suffix}.html").write_text(html, encoding="utf-8")

    print(f"Generierte HTMLs: {', '.join(out_dir.iterdir().__str__() for _ in [0])}")

    # 2) Shopware updaten
    sw = ShopwareClient(shop_url, client_id, client_secret)

    product_id = sw.find_product_id_by_number(repo_name)
    if not product_id:
        abort(f"Kein Produkt mit productNumber='{repo_name}' gefunden.")

    # Sprache-IDs ermitteln (de-DE, en-GB)
    lang_map: Dict[str, Optional[str]] = {
        "de": sw.search_language_id("de-DE"),
        "en": sw.search_language_id("en-GB"),
    }

    # Pro Locale beschreibung setzen
    for loc, html in html_per_locale.items():
        key = "de" if loc.startswith("de") else "en"
        sw_lang_id = lang_map.get(key)
        # Wenn Sprache nicht gefunden: ohne sw-language-id -> Default
        sw.patch_product_description(product_id, html, sw_language_id=sw_lang_id)

    # 3) Bilder hochladen und verknüpfen
    images = store.get("images", []) or []
    media_ids: List[Tuple[str, int]] = []
    cover_media_id: Optional[str] = None

    def is_preview_for_any_locale(img: Dict[str, Any]) -> bool:
        prev = img.get("preview", {})
        # Wenn irgendein Locale preview=true ist, nehmen wir es als Cover-Kandidat
        return any(bool(prev.get(lc)) for lc in prev.keys()) if isinstance(prev, dict) else False

    position = 1
    for img in images:
        file_rel = img.get("file")
        if not file_rel:
            continue
        fp = base_dir / file_rel
        if not fp.exists():
            abort(f"Bilddatei nicht gefunden: {fp}")
        mid = sw.upload_media(fp, repo_name=args.repo_name)
        media_ids.append((mid, position))
        if is_preview_for_any_locale(img):
            cover_media_id = mid
        position += 1

    if media_ids:
        sw.set_product_media(product_id, media_ids, cover_media_id)

    print("Sync erfolgreich abgeschlossen.")

if __name__ == "__main__":
    main()
