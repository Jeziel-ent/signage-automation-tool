"""
Batch signage renderer.

Reads data/products.json (one record per product variation) plus a base
config.json layout, overrides the text/image fields per record, and drives
CorelDrawEngine once per product to produce a .cdr, a PDF, and a PNG for
each one.

Usage:
    python batch_runner.py --products data/products.json --config config.json
"""

import argparse
import copy
import json
import sys
from pathlib import Path

from generate_layout import CorelDrawEngine

DEFAULT_PNG_DPI = 300

# Which config element each product field overrides.
_TEXT_FIELD_MAP = {
    "footer_text_en": "footer_text_en",
    "footer_text_ta": "footer_text_ta",
}
_IMAGE_ELEMENT_ID = "hero_product_box"


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        print(f"ERROR: Could not read '{path}': {exc}")
        sys.exit(1)


def build_product_config(base_config, product):
    """Return a deep copy of base_config with this product's text/image
    fields substituted into the matching elements."""
    config = copy.deepcopy(base_config)

    footer_en_text = f"{product['title_en']} — {product['price']}"
    footer_ta_text = product["title_ta"]

    for element in config["elements"]:
        if element["id"] == "footer_text_en":
            element["text"] = footer_en_text
        elif element["id"] == "footer_text_ta":
            element["text"] = footer_ta_text
        elif element["id"] == _IMAGE_ELEMENT_ID:
            element["type"] = "image"
            element["asset_path"] = product["hero_image_path"]

    return config


def run_batch(products_path, config_path, output_root):
    products = load_json(products_path)
    base_config = load_json(config_path)

    # Safety check: make sure every output subfolder exists before the batch
    # starts, and again defensively per-iteration below in case something
    # (or someone) removes one mid-run.
    output_root = Path(output_root).resolve()
    cdr_dir = output_root / "cdr"
    pdf_dir = output_root / "pdf"
    png_dir = output_root / "png"
    for d in (cdr_dir, pdf_dir, png_dir):
        d.mkdir(parents=True, exist_ok=True)
    print(f"Output directories ready: {cdr_dir}, {pdf_dir}, {png_dir}")

    total = len(products)
    results = []

    for i, product in enumerate(products, start=1):
        # A malformed record (missing product_id) shouldn't abort the whole
        # batch either -- fall back to a positional label so it still shows
        # up in the results instead of crashing before we can log it.
        product_id = product.get("product_id", f"product_{i}") if isinstance(product, dict) else f"product_{i}"
        print(f"[{i}/{total}] {product_id}: building layout...")

        try:
            for d in (cdr_dir, pdf_dir, png_dir):
                d.mkdir(parents=True, exist_ok=True)

            product_config = build_product_config(base_config, product)
            engine = CorelDrawEngine.from_config_dict(product_config, config_name=product_id)

            outcome = engine.run(
                save_path=cdr_dir / f"{product_id}.cdr",
                pdf_path=pdf_dir / f"{product_id}.pdf",
                png_path=png_dir / f"{product_id}.png",
                png_dpi=DEFAULT_PNG_DPI,
            )

            cdr_path = cdr_dir / f"{product_id}.cdr"
            pdf_path = pdf_dir / f"{product_id}.pdf"
            png_path = png_dir / f"{product_id}.png"

            files_exist = {
                "cdr": cdr_path.exists(),
                "pdf": pdf_path.exists(),
                "png": png_path.exists(),
            }
            ok = all(files_exist.values())
            status = "OK" if ok else "INCOMPLETE"
            print(f"[{i}/{total}] {product_id}: {status} "
                  f"(cdr={files_exist['cdr']}, pdf={files_exist['pdf']}, png={files_exist['png']})")

            results.append({
                "product_id": product_id,
                "ok": ok,
                "error": None,
                "files": files_exist,
                "engine_outcome": outcome,
            })

        except Exception as exc:
            # One product's failure (a bad asset path, a CorelDRAW COM
            # error, a malformed record, ...) must not take down the rest
            # of the batch -- log it and move on to the next product.
            print(f"[{i}/{total}] {product_id}: FAILED - {exc}")
            results.append({
                "product_id": product_id,
                "ok": False,
                "error": str(exc),
                "files": {"cdr": False, "pdf": False, "png": False},
                "engine_outcome": None,
            })

    succeeded = sum(1 for r in results if r["ok"])
    print(f"\nBatch complete: {succeeded}/{total} product(s) fully rendered "
          f"(cdr + pdf + png all present).")
    for r in results:
        if not r["ok"]:
            reason = f" ({r['error']})" if r["error"] else ""
            print(f"  INCOMPLETE: {r['product_id']} -> {r['files']}{reason}")

    return results


def parse_args():
    parser = argparse.ArgumentParser(description="Batch-render signage from a product dataset.")
    parser.add_argument("--products", default="data/products.json", help="Path to the product dataset JSON.")
    parser.add_argument("--config", default="config.json", help="Path to the base layout config JSON.")
    parser.add_argument("--output-dir", default="output", help="Root output directory (cdr/pdf/png subfolders created inside it).")
    return parser.parse_args()


def main():
    args = parse_args()
    run_batch(args.products, args.config, args.output_dir)


if __name__ == "__main__":
    main()
