"""Rebuild pinned, offline file-preview assets without npm or a frontend build."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import tarfile
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "sona_code/web/static/vendor"
PACKAGES = {
    "prismjs": {
        "version": "1.30.0",
        "integrity": "sha512-DEvV2ZF2r2/63V+tK8hQvrR2ZGn10srHbXviTlcv7Kpzw8jWiNTqbVgjO3IY8RxrrOUF8VPMQQFysYYYv0YZxw==",
    },
    "pdfjs-dist": {
        "version": "5.4.624",
        "integrity": "sha512-sm6TxKTtWv1Oh6n3C6J6a8odejb5uO4A4zo/2dgkHuC0iu8ZMAXOezEODkVaoVp8nX1Xzr+0WxFJJmUr45hQzg==",
    },
}
PRISM_LANGUAGES = (
    "markup", "css", "clike", "javascript", "python", "bash", "json", "yaml",
    "typescript", "jsx", "tsx", "rust", "go", "java", "c", "cpp", "csharp",
    "sql", "powershell", "diff", "markdown", "toml", "ini", "docker", "scss",
    "less", "ruby", "markup-templating", "php", "swift", "kotlin", "dart",
)


def archive(name: str) -> tarfile.TarFile:
    package = PACKAGES[name]
    url = f"https://registry.npmjs.org/{name}/-/{name}-{package['version']}.tgz"
    cache = ROOT / "build/preview-assets" / f"{name}-{package['version']}.tgz"
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not cache.exists():
        with urllib.request.urlopen(url, timeout=60) as response:
            cache.write_bytes(response.read())
    payload = cache.read_bytes()
    digest = "sha512-" + base64.b64encode(hashlib.sha512(payload).digest()).decode("ascii")
    if digest != package["integrity"]:
        raise SystemExit(f"Integrity mismatch: {name}; remove {cache} and retry")
    return tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz")


def read(source: tarfile.TarFile, name: str) -> bytes:
    member = source.getmember("package/" + name)
    if not member.isfile():
        raise SystemExit(f"Expected regular package file: {name}")
    return source.extractfile(member).read()


def main() -> None:
    VENDOR.mkdir(parents=True, exist_ok=True)
    with archive("prismjs") as source:
        parts = [b"/* Prism 1.30.0, MIT license; see prism-LICENSE. */\n",
                 b"window.Prism = { manual: true };\n",
                 read(source, "components/prism-core.min.js")]
        for language in PRISM_LANGUAGES:
            parts.append(read(source, f"components/prism-{language}.min.js"))
        parts.append(read(source, "plugins/line-numbers/prism-line-numbers.min.js"))
        (VENDOR / "prism.js").write_bytes(b"\n".join(parts) + b"\n")
        (VENDOR / "prism-LICENSE").write_bytes(read(source, "LICENSE"))
    destination = VENDOR / "pdfjs"
    destination.mkdir(exist_ok=True)
    with archive("pdfjs-dist") as source:
        (destination / "pdf.mjs").write_bytes(read(source, "build/pdf.min.mjs"))
        (destination / "pdf.worker.mjs").write_bytes(read(source, "build/pdf.worker.min.mjs"))
        (destination / "LICENSE").write_bytes(read(source, "LICENSE"))
        for member in source.getmembers():
            relative = Path(member.name).relative_to("package") if member.name.startswith("package/") else None
            if relative is None or relative.parts[0] not in {"cmaps", "standard_fonts", "wasm"} or not member.isfile():
                continue
            if ".." in relative.parts:
                raise SystemExit("Unexpected archive path")
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.extractfile(member).read())
    manifest = {"packages": PACKAGES, "prismLanguages": PRISM_LANGUAGES,
                "files": {str(path.relative_to(VENDOR)): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in sorted(destination.rglob("*")) if path.is_file()}}
    manifest["files"]["prism.js"] = hashlib.sha256((VENDOR / "prism.js").read_bytes()).hexdigest()
    (VENDOR / "file-preview.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Prepared Prism {PACKAGES['prismjs']['version']} and PDF.js {PACKAGES['pdfjs-dist']['version']}")


if __name__ == "__main__":
    main()
