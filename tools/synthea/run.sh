#!/bin/sh
# Runs INSIDE the `synthea` compose container (never on the host):
#   docker compose --profile tools run --rm synthea     (or: make synthea)
# Downloads the pinned Synthea jar (untrusted input: it only ever executes in this container,
# with ./data as the only host mount), verifies it, then generates a synthetic FHIR R4 cohort.
set -eu

SYNTHEA_VERSION="${SYNTHEA_VERSION:-v3.3.0}"
SYNTHEA_SEED="${SYNTHEA_SEED:-20261017}"
SYNTHEA_POPULATION="${SYNTHEA_POPULATION:-300}"
# Reference/end date (YYYYMMDD) for ages and the end of simulated history. Default = the date of
# SIM_CLOCK_START (2026-01-05), so the cohort is a pure function of (version, seed, population,
# date), not of wall clock, and 40-75 means "aged 40-75 on the simulation start date" (Abu Dhabi policy age range,
# docs/evidence.md section 8).
SYNTHEA_REFERENCE_DATE="${SYNTHEA_REFERENCE_DATE:-20260105}"
SYNTHEA_AGE_RANGE="${SYNTHEA_AGE_RANGE:-40-75}"
SYNTHEA_STATE="${SYNTHEA_STATE:-Massachusetts}"

DATA=/data/synthea
JAR_DIR="$DATA/jar"
OUT_DIR="$DATA/out"
JAR="$JAR_DIR/synthea-with-dependencies-$SYNTHEA_VERSION.jar"
URL="https://github.com/synthetichealth/synthea/releases/download/$SYNTHEA_VERSION/synthea-with-dependencies.jar"
SUMS=/tools/SHA256SUMS

mkdir -p "$JAR_DIR"

verify_jar() {
  command -v unzip >/dev/null 2>&1 || {
    echo "[synthea] installing unzip (needed to verify the jar)"
    DEBIAN_FRONTEND=noninteractive apt-get update -qq >/dev/null 2>&1 \
      && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq unzip >/dev/null 2>&1
  }
  unzip -l "$1" | grep -q 'App\.class' || { echo "[synthea] ERROR: $1 is not a valid Synthea jar (no App.class)" >&2; return 1; }
  actual=$(sha256sum "$1" | cut -d' ' -f1)
  pinned=$(grep " synthea-with-dependencies-$SYNTHEA_VERSION.jar\$" "$SUMS" 2>/dev/null | cut -d' ' -f1 || true)
  if [ -n "$pinned" ] && [ "$pinned" != "$actual" ]; then
    echo "[synthea] ERROR: sha256 mismatch for $1 (expected $pinned, got $actual)" >&2; return 1
  fi
  echo "[synthea] jar ok: sha256=$actual${pinned:+ (matches pinned)}"
}

if [ -f "$JAR" ] && verify_jar "$JAR"; then
  echo "[synthea] using cached jar $JAR"
else
  rm -f "$JAR"
  echo "[synthea] downloading $URL"
  curl -fsSL --retry 3 -o "$JAR.part" "$URL" || { echo "[synthea] ERROR: download failed for $URL" >&2; rm -f "$JAR.part"; exit 1; }
  mv "$JAR.part" "$JAR"
  verify_jar "$JAR" || { rm -f "$JAR"; exit 1; }
fi

echo "[synthea] wiping $OUT_DIR"
rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

echo "[synthea] version: $SYNTHEA_VERSION seed=$SYNTHEA_SEED population=$SYNTHEA_POPULATION age=$SYNTHEA_AGE_RANGE state=$SYNTHEA_STATE reference_date=$SYNTHEA_REFERENCE_DATE"
start=$(date +%s)
set -x
java -jar "$JAR" \
  -s "$SYNTHEA_SEED" \
  -cs "$SYNTHEA_SEED" \
  -r "$SYNTHEA_REFERENCE_DATE" \
  -e "$SYNTHEA_REFERENCE_DATE" \
  -p "$SYNTHEA_POPULATION" \
  -a "$SYNTHEA_AGE_RANGE" \
  --exporter.years_of_history=5 \
  --exporter.fhir.export=true \
  --exporter.hospital.fhir.export=true \
  --exporter.practitioner.fhir.export=true \
  --exporter.fhir.use_us_core_ig=false \
  --exporter.baseDirectory="$OUT_DIR" \
  "$SYNTHEA_STATE"
set +x
elapsed=$(( $(date +%s) - start ))

fhir="$OUT_DIR/fhir"
patients=$(find "$fhir" -name '*.json' ! -name 'hospitalInformation*' ! -name 'practitionerInformation*' | wc -l)
echo "[synthea] done in ${elapsed}s: $patients patient bundles in $fhir"
