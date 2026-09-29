"""Import the official administrative region workbook into PostgreSQL."""
import argparse
import asyncio
import sys
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from sqlalchemy import delete

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.database import AsyncSessionFactory, engine
from app.modules.regions.models import AdministrativeRegion

NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


def workbook_rows(path: Path):
    with ZipFile(path) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = [
                "".join(text.text or "" for text in item.iter("{%s}t" % NS["m"]))
                for item in root.findall("m:si", NS)
            ]
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        relmap = {item.attrib["Id"]: item.attrib["Target"] for item in relationships}
        sheet = workbook.find("m:sheets/m:sheet", NS)
        if sheet is None:
            raise RuntimeError("Workbook tidak memiliki sheet")
        target = relmap[sheet.attrib["{%s}id" % NS["r"]]]
        target = "xl/" + target.lstrip("/") if not target.startswith("xl/") else target
        worksheet = ET.fromstring(archive.read(target))
        rows = worksheet.findall(".//m:sheetData/m:row", NS)
        for row in rows[1:]:
            values = {}
            for cell in row.findall("m:c", NS):
                value = cell.find("m:v", NS)
                text = "" if value is None else value.text or ""
                if cell.attrib.get("t") == "s" and text:
                    text = shared[int(text)]
                values[cell.attrib.get("r", "").rstrip("0123456789")] = text.strip()
            code, name = values.get("A", ""), values.get("B", "")
            if code and name:
                yield code, name


def region_row(code: str, name: str) -> dict:
    lengths = {2: "province", 4: "regency", 6: "district", 10: "village"}
    level = lengths.get(len(code))
    if level is None:
        raise ValueError(f"Panjang kode wilayah tidak dikenal: {code}")
    parent_code = {"province": None, "regency": code[:2], "district": code[:4], "village": code[:6]}[level]
    return {"code": code, "name": name, "level": level, "parent_code": parent_code}


async def import_regions(path: Path, replace: bool) -> int:
    rows = [region_row(code, name) for code, name in workbook_rows(path)]
    available_codes = {row["code"] for row in rows}
    valid_rows = [row for row in rows if row["parent_code"] is None or row["parent_code"] in available_codes]
    skipped = len(rows) - len(valid_rows)
    rows = valid_rows
    level_order = {"province": 0, "regency": 1, "district": 2, "village": 3}
    rows.sort(key=lambda row: (level_order[row["level"]], row["code"]))
    async with AsyncSessionFactory() as db:
        if replace:
            await db.execute(delete(AdministrativeRegion))
        for offset in range(0, len(rows), 1000):
            db.add_all(AdministrativeRegion(**row) for row in rows[offset : offset + 1000])
            await db.flush()
        await db.commit()
    if skipped:
        print(f"Skipped {skipped} orphan region rows with missing parent codes.")
    return len(rows)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workbook", type=Path, default=PROJECT_ROOT / "docs" / "kode wilayah.xlsx")
    parser.add_argument("--replace", action="store_true", help="Replace the existing master data")
    args = parser.parse_args()
    try:
        count = await import_regions(args.workbook, args.replace)
        print(f"Imported {count} administrative regions from {args.workbook}.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
