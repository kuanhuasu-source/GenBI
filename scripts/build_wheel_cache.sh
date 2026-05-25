#!/usr/bin/env bash
# scripts/build_wheel_cache.sh — Download all GenBI deps as wheel files
# for air-gap deployment.
#
# Run on internet-connected machine:
#   bash scripts/build_wheel_cache.sh
#
# Output:./wheels/ — copy whole dir to air-gap machine, then:
#   pip install --no-index --find-links=./wheels -r requirements.txt

set -e

WHEEL_DIR="${WHEEL_DIR:-./wheels}"
PY="${PY:-python}"

# Target platform — Mac arm64 / Linux x86_64 / etc.
# Empty = native(在中介機跑 = 為中介機架構抓 wheel)
# 對 cross-arch:--platform manylinux2014_x86_64 --python-version 310
PLATFORM_ARGS="${PLATFORM_ARGS:-}"

echo "=========================================="
echo "GenBI · Wheel Cache Builder"
echo "=========================================="
echo "WHEEL_DIR  = $WHEEL_DIR"
echo "PY         = $PY"
echo "PLATFORM   = ${PLATFORM_ARGS:-(native)}"
echo

mkdir -p "$WHEEL_DIR"

# 確保 pip 是新的
$PY -m pip install --upgrade pip wheel

# 抓所有 requirements.txt 內的 wheel(含相依)
echo
echo "── Downloading wheels from requirements.txt ──"
$PY -m pip download \
    -r requirements.txt \
    -d "$WHEEL_DIR" \
    $PLATFORM_ARGS

# Optional: dev deps(test runner / linter)
if [ -f requirements-dev.txt ]; then
    echo
    echo "── Downloading wheels from requirements-dev.txt ──"
    $PY -m pip download \
        -r requirements-dev.txt \
        -d "$WHEEL_DIR" \
        $PLATFORM_ARGS
fi

# 看一下結果
echo
echo "=========================================="
echo "✅ Wheel cache built at $WHEEL_DIR"
echo "=========================================="
echo "Total: $(ls "$WHEEL_DIR" | wc -l) wheels, $(du -sh "$WHEEL_DIR" | cut -f1)"
echo
echo "Next steps:"
echo "  1. Tar:   tar czf wheels.tar.gz $WHEEL_DIR/"
echo "  2. Copy to air-gap machine"
echo "  3. Extract + install:"
echo "       tar xzf wheels.tar.gz"
echo "       pip install --no-index --find-links=./wheels -r requirements.txt"
echo
echo "Cross-arch hint(中介機跟部署機不同架構):"
echo "  PLATFORM_ARGS='--platform manylinux2014_x86_64 --python-version 310 --only-binary=:all:' \\"
echo "    bash scripts/build_wheel_cache.sh"
