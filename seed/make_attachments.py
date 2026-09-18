"""Deterministically (re)generate the seed attachments, manifest.json and emails.json.

    python seed/make_attachments.py            # writes into seed/attachments and seed/emails.json
    python seed/make_attachments.py --out DIR  # writes attachments + manifest into DIR (used by tests)

Determinism: reportlab runs in `invariant` mode (fixed timestamps / document IDs), ZIP entries carry a fixed
date, the corrupt file comes from a seeded RNG. Regenerating must produce byte-identical files — the test
suite checks the sha256 of every attachment against manifest.json.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import random
import sys
import zipfile
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).resolve().parent))
from seed_emails import COMPANY, CSV_INVOICES, EMAILS, INVOICES, OTHER_PDFS, SUPPLIERS, ZIPS  # noqa: E402

SEED_DIR = Path(__file__).resolve().parent
VAT_RATE = Decimal("0.20")
TWO = Decimal("0.01")


def money(x: Decimal) -> str:
    return f"{x.quantize(TWO, rounding=ROUND_HALF_UP):,.2f}"


def totals(items: list[tuple[str, int, float]]) -> tuple[list[tuple[str, int, Decimal, Decimal]], Decimal, Decimal, Decimal]:
    rows = []
    net = Decimal("0")
    for desc, qty, unit in items:
        unit_d = Decimal(str(unit))
        line = (unit_d * qty).quantize(TWO, rounding=ROUND_HALF_UP)
        rows.append((desc, qty, unit_d, line))
        net += line
    vat = (net * VAT_RATE).quantize(TWO, rounding=ROUND_HALF_UP)
    return rows, net, vat, net + vat


# --- PDF builders ----------------------------------------------------------------------------------------

def _canvas(buf: io.BytesIO, title: str) -> canvas.Canvas:
    c = canvas.Canvas(buf, pagesize=A4, invariant=1, pageCompression=0)
    c.setTitle(title)
    c.setAuthor("seed generator")
    return c


def invoice_pdf(spec: dict) -> bytes:
    sup = SUPPLIERS[spec["supplier"]]
    rows, net, vat, total = totals(spec["items"])
    buf = io.BytesIO()
    c = _canvas(buf, f"Invoice {spec['number']}")
    w, h = A4
    y = h - 25 * mm

    c.setFont("Helvetica-Bold", 16)
    c.drawString(20 * mm, y, sup["name"])
    c.setFont("Helvetica", 9)
    for line in sup["address"] + [sup["email"], f"VAT no. {sup['vat']}"]:
        y -= 5 * mm
        c.drawString(20 * mm, y, line)

    c.setFont("Helvetica-Bold", 22)
    c.drawRightString(w - 20 * mm, h - 25 * mm, "INVOICE")
    c.setFont("Helvetica", 10)
    c.drawRightString(w - 20 * mm, h - 33 * mm, f"Invoice number: {spec['number']}")
    c.drawRightString(w - 20 * mm, h - 39 * mm, f"Invoice date: {spec['date']}")
    c.drawRightString(w - 20 * mm, h - 45 * mm, f"Due date: {spec['due']}")

    y -= 14 * mm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(20 * mm, y, "Bill to:")
    c.setFont("Helvetica", 10)
    for line in [COMPANY["name"], *COMPANY["address"], COMPANY["accounts_email"]]:
        y -= 5 * mm
        c.drawString(20 * mm, y, line)

    if spec.get("note"):
        y -= 9 * mm
        c.setFont("Helvetica-Oblique", 9)
        c.drawString(20 * mm, y, spec["note"])

    y -= 14 * mm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(20 * mm, y, "Description")
    c.drawRightString(120 * mm, y, "Qty")
    c.drawRightString(150 * mm, y, "Unit (GBP)")
    c.drawRightString(w - 20 * mm, y, "Net (GBP)")
    c.line(20 * mm, y - 2 * mm, w - 20 * mm, y - 2 * mm)
    c.setFont("Helvetica", 10)
    for desc, qty, unit, line in rows:
        y -= 7 * mm
        c.drawString(20 * mm, y, desc)
        c.drawRightString(120 * mm, y, str(qty))
        c.drawRightString(150 * mm, y, money(unit))
        c.drawRightString(w - 20 * mm, y, money(line))

    y -= 12 * mm
    c.line(110 * mm, y + 4 * mm, w - 20 * mm, y + 4 * mm)
    c.drawRightString(150 * mm, y, "Subtotal (net)")
    c.drawRightString(w - 20 * mm, y, money(net))
    y -= 6 * mm
    c.drawRightString(150 * mm, y, "VAT @ 20%")
    c.drawRightString(w - 20 * mm, y, money(vat))
    y -= 7 * mm
    c.setFont("Helvetica-Bold", 12)
    c.drawRightString(150 * mm, y, "TOTAL DUE")
    c.drawRightString(w - 20 * mm, y, f"GBP {money(total)}")

    y -= 18 * mm
    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, y, f"Payment by BACS to {sup['name']} — sort code {spec.get('sort', sup['sort'])}, account {spec.get('acct', sup['acct'])}. "
                             f"Please quote {spec['number']}.")
    y -= 5 * mm
    c.drawString(20 * mm, y, f"Payment terms: due {spec['due']}. Queries: {sup['email']}")

    c.showPage()
    c.save()
    return buf.getvalue()


def invoice_csv(spec: dict) -> bytes:
    sup = SUPPLIERS[spec["supplier"]]
    rows, net, vat, total = totals(spec["items"])
    buf = io.StringIO()
    wr = csv.writer(buf, lineterminator="\n")
    wr.writerow(["supplier", "supplier_email", "invoice_number", "invoice_date", "due_date", "currency"])
    wr.writerow([sup["name"], sup["email"], spec["number"], spec["date"], spec["due"], "GBP"])
    wr.writerow([])
    wr.writerow(["description", "qty", "unit_price_net", "line_net"])
    for desc, qty, unit, line in rows:
        wr.writerow([desc, qty, f"{unit:.2f}", f"{line:.2f}"])
    wr.writerow([])
    wr.writerow(["subtotal_net", f"{net:.2f}"])
    wr.writerow(["vat_20pct", f"{vat:.2f}"])
    wr.writerow(["total_due", f"{total:.2f}"])
    wr.writerow(["bill_to", COMPANY["name"]])
    return buf.getvalue().encode("utf-8")


def marketing_pdf() -> bytes:
    sup = SUPPLIERS["brightline"]
    buf = io.BytesIO()
    c = _canvas(buf, "Brightline Spring Catalogue 2027")
    w, h = A4
    c.setFont("Helvetica-Bold", 26)
    c.drawCentredString(w / 2, h - 40 * mm, "Brightline Uniforms")
    c.setFont("Helvetica-Bold", 18)
    c.drawCentredString(w / 2, h - 52 * mm, "Spring Catalogue 2027")
    c.setFont("Helvetica", 11)
    lines = [
        "New season workwear for facilities teams.",
        "",
        "• Recycled-polyester polo shirts in 8 colours",
        "• Lightweight softshell jackets with reflective trim",
        "• Slip-resistant safety trainers — now in wide fit",
        "• Embroidery from 10 units, no setup fee",
        "",
        "15% off all orders placed before 31 March 2027 with code SPRING15.",
        "",
        "This is a marketing brochure, not an invoice. No payment is due.",
        f"Contact {sup['email']} or visit brightlineuniforms.co.uk/catalogue",
    ]
    y = h - 70 * mm
    for line in lines:
        c.drawString(25 * mm, y, line)
        y -= 7 * mm
    c.showPage()
    c.save()
    return buf.getvalue()


def letter_pdf() -> bytes:
    buf = io.BytesIO()
    c = _canvas(buf, "Westgate Medical Centre — service complaint")
    w, h = A4
    c.setFont("Helvetica-Bold", 14)
    c.drawString(20 * mm, h - 25 * mm, "Westgate Medical Centre")
    c.setFont("Helvetica", 9)
    for i, line in enumerate(["8 Westgate Road, Bristol BS3 1WW", "practicemanager@westgatemedical.co.uk", "Account WMC-1180"]):
        c.drawString(20 * mm, h - 31 * mm - i * 5 * mm, line)
    c.setFont("Helvetica", 10)
    c.drawString(20 * mm, h - 55 * mm, "12 September 2026")
    c.drawString(20 * mm, h - 65 * mm, "Northwind Facilities Ltd — Operations")
    c.setFont("Helvetica-Bold", 11)
    c.drawString(20 * mm, h - 80 * mm, "Re: Service complaint — treatment rooms, week of 8 September (account WMC-1180)")
    c.setFont("Helvetica", 10)
    body = [
        "Dear Northwind,",
        "",
        "On three evenings this week (8, 9 and 11 September) treatment rooms 2 and 4 were not cleaned to the",
        "clinical standard in our contract: clinical waste bins were not emptied and the couches were not wiped",
        "down. Our nurses had to do this themselves before morning clinics.",
        "",
        "We are requesting a credit of £90.00 against this month's invoice for the three missed room cleans,",
        "and written confirmation of the corrective action taken with the evening team.",
        "",
        "Yours sincerely,",
        "",
        "Aisha Rahman",
        "Practice Manager, Westgate Medical Centre",
    ]
    y = h - 92 * mm
    for line in body:
        c.drawString(20 * mm, y, line)
        y -= 6 * mm
    c.showPage()
    c.save()
    return buf.getvalue()


def corrupt_pdf() -> bytes:
    rng = random.Random(1611)
    return b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< /Type /Catalog" + bytes(rng.getrandbits(8) for _ in range(2048))


def make_zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 9, 11, 9, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)
    return buf.getvalue()


# --- build everything --------------------------------------------------------------------------------------

def build_files() -> tuple[dict[str, bytes], dict[str, dict]]:
    """Returns (files, invoice_totals). files maps filename -> bytes (zip members are also listed on their own)."""
    files: dict[str, bytes] = {}
    inv_totals: dict[str, dict] = {}
    for name, spec in INVOICES.items():
        files[name] = invoice_pdf(spec)
        _, net, vat, total = totals(spec["items"])
        inv_totals[name] = {"invoice_number": spec["number"], "total": f"{total:.2f}", "currency": "GBP",
                            "supplier": SUPPLIERS[spec["supplier"]]["name"]}
    for name, spec in CSV_INVOICES.items():
        files[name] = invoice_csv(spec)
        _, net, vat, total = totals(spec["items"])
        inv_totals[name] = {"invoice_number": spec["number"], "total": f"{total:.2f}", "currency": "GBP",
                            "supplier": SUPPLIERS[spec["supplier"]]["name"]}
    builders = {"marketing": marketing_pdf, "letter": letter_pdf, "corrupt": corrupt_pdf}
    for name, spec in OTHER_PDFS.items():
        files[name] = builders[spec["kind"]]()
    for zname, members in ZIPS.items():
        files[zname] = make_zip({m: files[m] for m in members})
    return files, inv_totals


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_emails_json(inv_totals: dict[str, dict], manifest: dict[str, dict]) -> dict:
    emails = []
    for e in EMAILS:
        e = json.loads(json.dumps(e))  # deep copy, plain JSON types
        e["message_id"] = f"seed-{e['seed_no']:02d}"
        for d in e["expected"]["documents"]:
            fn = d["filename"]
            if fn in inv_totals and d["doc_type"] == "supplier_invoice" and d.get("party"):
                d.setdefault("extracted", {}).update({k: inv_totals[fn][k] for k in ("invoice_number", "total", "currency")})
            if fn in manifest:
                d["sha256"] = manifest[fn]["sha256"]
        for fn in e["attachments"]:
            assert fn in manifest, f"seed {e['seed_no']}: attachment {fn} was not generated"
        emails.append(e)
    return {"company": COMPANY, "generator": "seed/make_attachments.py", "count": len(emails), "emails": emails}


def main(out_dir: Path, write_emails_json: bool) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    files, inv_totals = build_files()
    manifest: dict[str, dict] = {}
    for name, data in files.items():
        (out_dir / name).write_bytes(data)
        manifest[name] = {"sha256": sha256(data), "size": len(data)}
    for zname, members in ZIPS.items():
        manifest[zname]["members"] = members
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    if write_emails_json:
        (SEED_DIR / "emails.json").write_text(json.dumps(build_emails_json(inv_totals, manifest), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(files)} attachments to {out_dir}")
    for name, t in inv_totals.items():
        print(f"  {name:28s} {t['invoice_number']:10s} GBP {t['total']:>9s}  {t['supplier']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=SEED_DIR / "attachments")
    args = ap.parse_args()
    main(args.out, write_emails_json=(args.out == SEED_DIR / "attachments"))
