#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "$0")/.." && pwd)"
build_python="${BUILD_PYTHON:-$project_dir/.venv-build/bin/python}"

if [[ ! -x "$build_python" ]]; then
  echo "缺少桌面构建环境：$build_python" >&2
  echo "请先使用 Python 3.10+ 创建 .venv-build 并安装 .[desktop]。" >&2
  exit 1
fi

case "$(uname -s)-$(uname -m)" in
  Darwin-arm64) target_triple="aarch64-apple-darwin" ;;
  Darwin-x86_64) target_triple="x86_64-apple-darwin" ;;
  *)
    echo "当前构建脚本暂只支持 macOS arm64/x86_64。" >&2
    exit 1
    ;;
esac

output_dir="$project_dir/build/sidecar"
binary_dir="$project_dir/src-tauri/binaries"
binary_name="sona-code-sidecar"

mkdir -p "$output_dir" "$binary_dir"
"$build_python" -m PyInstaller \
  --noconfirm \
  --clean \
  --distpath "$output_dir/dist" \
  --workpath "$output_dir/work" \
  "$project_dir/packaging/sidecar.spec"

cp "$output_dir/dist/$binary_name" "$binary_dir/$binary_name-$target_triple"
chmod +x "$binary_dir/$binary_name-$target_triple"
echo "sidecar: $binary_dir/$binary_name-$target_triple"
