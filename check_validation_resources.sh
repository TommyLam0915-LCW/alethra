#!/bin/bash
# Two separate admission checks, because the two workloads are not the same risk.
#
#   ./check_validation_resources.sh --light   targeted pytest / collection / tsc
#   ./check_validation_resources.sh --build   Next.js production build + smoke
#
# Read-only: measures the host, writes nothing, signals nothing. Exit 0 means
# "allowed to attempt", never "the work will succeed". Exit 3 means blocked.
#
# Light thresholds are derived from the estimated write footprint of a targeted
# suite (test database < 200 MiB, logs < 50 MiB) plus headroom, and from spare
# CPU: a run that cannot be interrupted promptly is not a light run.
#
# Build keeps the 10 GiB disk line that start_e2e_research_flow.sh already uses
# as its image-build stop line (FREE_DB_BUILD_MIB=10240), because Next.js writes
# a full client+server output tree and cleans its own distDir, so the space must
# exist before the build starts, not during it.
#
# Every threshold can be overridden, but only explicitly, by name:
#   ALETHRA_GATE_DISK_MIN_KIB  ALETHRA_GATE_RAM_MIN_MIB  ALETHRA_GATE_SWAP_MIN_MIB
#   ALETHRA_GATE_PRESSURE_MIN_PCT  ALETHRA_GATE_LOAD_MAX_PCT_OF_NCPU
set -u

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
MODE=""

usage() {
  cat <<'EOF'
usage: check_validation_resources.sh --light|--build

  --light   admit a targeted test/collection/typecheck run
  --build   admit a production frontend build (10 GiB disk line + RAM/swap/pressure)

exit: 0 admitted (preliminary), 2 bad usage, 3 blocked.
Thresholds are a preliminary operational minimum, not proof of success.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --light | --build) MODE=${1#--} ;;
    -h | --help) usage; exit 0 ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done
if [ -z "$MODE" ]; then
  echo "a mode is required: --light or --build" >&2
  usage >&2
  exit 2
fi

if [ "$MODE" = "build" ]; then
  DISK_MIN_KIB=${ALETHRA_GATE_DISK_MIN_KIB:-10485760}   # 10 GiB
  RAM_MIN_MIB=${ALETHRA_GATE_RAM_MIN_MIB:-2048}
  SWAP_MIN_MIB=${ALETHRA_GATE_SWAP_MIN_MIB:-3072}
  PRESSURE_MIN_PCT=${ALETHRA_GATE_PRESSURE_MIN_PCT:-40}
  LOAD_MAX_PCT_OF_NCPU=${ALETHRA_GATE_LOAD_MAX_PCT_OF_NCPU:-100}
else
  DISK_MIN_KIB=${ALETHRA_GATE_DISK_MIN_KIB:-1048576}    # 1 GiB
  RAM_MIN_MIB=${ALETHRA_GATE_RAM_MIN_MIB:-512}
  SWAP_MIN_MIB=${ALETHRA_GATE_SWAP_MIN_MIB:-512}
  PRESSURE_MIN_PCT=${ALETHRA_GATE_PRESSURE_MIN_PCT:-20}
  LOAD_MAX_PCT_OF_NCPU=${ALETHRA_GATE_LOAD_MAX_PCT_OF_NCPU:-200}
fi

for name in DISK_MIN_KIB RAM_MIN_MIB SWAP_MIN_MIB PRESSURE_MIN_PCT LOAD_MAX_PCT_OF_NCPU; do
  value=$(eval "printf '%s' \"\$$name\"")
  case "$value" in
    '' | *[!0-9]*)
      echo "refusing: $name must be a non-negative integer, got '$value'" >&2
      exit 2
      ;;
  esac
done

fail=0
unknown=0
note() { echo "gate: $*" >&2; }
forced_fail="no"

# --- CPU ---------------------------------------------------------------------
ncpu=$(sysctl -n hw.ncpu 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)
case "$ncpu" in '' | *[!0-9]*) ncpu=1 ;; esac

# --- disk --------------------------------------------------------------------
disk_avail_kib=$(df -k "$ROOT" 2>/dev/null | awk 'NR==2 {print $4}')
case "$disk_avail_kib" in '' | *[!0-9]*) disk_avail_kib="" ;; esac
disk_mib=""
disk_ok="unknown"
if [ -n "$disk_avail_kib" ]; then
  disk_mib=$((disk_avail_kib / 1024))
  disk_ok="no"
  [ "$disk_avail_kib" -ge "$DISK_MIN_KIB" ] && disk_ok="yes"
fi

# --- memory / swap / pressure / load -----------------------------------------
ram_avail_mib=""
swap_total_mib=""
swap_free_mib=""
pressure_pct=""
load1=""
wired_mib="-"
compressor_mib="-"

if [ "$(uname -s)" = "Darwin" ]; then
  page_size=$(sysctl -n hw.pagesize 2>/dev/null || echo 4096)
  vmstat_out=$(vm_stat 2>/dev/null || true)
  page() {
    printf '%s\n' "$vmstat_out" |
      awk -F: -v k="$1" '$1 ~ k {gsub(/[^0-9]/,"",$2); print $2; exit}'
  }
  p_free=$(page 'Pages free')
  p_inactive=$(page 'Pages inactive')
  p_spec=$(page 'Pages speculative')
  p_wired=$(page 'Pages wired down')
  p_comp=$(page 'Pages occupied by compressor')
  if [ -n "$p_free" ] && [ -n "$p_inactive" ]; then
    p_spec=${p_spec:-0}
    ram_avail_mib=$(((p_free + p_inactive + p_spec) * page_size / 1048576))
    [ -n "$p_wired" ] && wired_mib=$((p_wired * page_size / 1048576))
    [ -n "$p_comp" ] && compressor_mib=$((p_comp * page_size / 1048576))
  fi
  swap_out=$(sysctl -n vm.swapusage 2>/dev/null || true)
  swap_total_mib=$(printf '%s' "$swap_out" | sed -n 's/.*total = \([0-9.]*\)M.*/\1/p' | cut -d. -f1)
  swap_free_mib=$(printf '%s' "$swap_out" | sed -n 's/.*free = \([0-9.]*\)M.*/\1/p' | cut -d. -f1)
  pressure_out=$(memory_pressure -Q 2>&1 || true)
  pressure_pct=$(printf '%s' "$pressure_out" | sed -n 's/.*free percentage: *\([0-9][0-9]*\)%.*/\1/p' | tail -1)
  load1=$(sysctl -n vm.loadavg 2>/dev/null | awk '{print $2}')
elif [ -s /proc/meminfo ]; then
  meminfo() { awk -v k="$1" '$1 == k":" {print $2; exit}' /proc/meminfo; }
  mem_total_kib=$(meminfo MemTotal)
  mem_avail_kib=$(meminfo MemAvailable)
  swap_total_mib=$(( $(meminfo SwapTotal) / 1024 ))
  swap_free_mib=$(( $(meminfo SwapFree) / 1024 ))
  if [ -n "$mem_avail_kib" ]; then
    ram_avail_mib=$((mem_avail_kib / 1024))
  fi
  if [ -n "$mem_total_kib" ] && [ "$mem_total_kib" -gt 0 ]; then
    # Linux has no memory_pressure(1); free/total is a conservative analogue.
    pressure_pct=$((mem_avail_kib * 100 / mem_total_kib))
  fi
  load1=$(awk '{print $1}' /proc/loadavg 2>/dev/null)
  note "pressure on Linux is MemAvailable/MemTotal, an analogue of memory_pressure(1)"
fi

ram_ok="unknown"
if [ -n "$ram_avail_mib" ]; then
  ram_ok="no"
  [ "$ram_avail_mib" -ge "$RAM_MIN_MIB" ] && ram_ok="yes"
fi

swap_ok="unknown"
if [ -n "$swap_free_mib" ]; then
  swap_ok="no"
  [ "$swap_free_mib" -ge "$SWAP_MIN_MIB" ] && swap_ok="yes"
fi

pressure_ok="unknown"
if [ -n "$pressure_pct" ]; then
  pressure_ok="no"
  [ "$pressure_pct" -ge "$PRESSURE_MIN_PCT" ] && pressure_ok="yes"
fi

load_x100=""
load_ok="unknown"
if [ -n "$load1" ]; then
  load_x100=$(awk -v v="$load1" 'BEGIN {printf "%d", v * 100}')
  load_ok="no"
  [ "$load_x100" -le $((ncpu * LOAD_MAX_PCT_OF_NCPU)) ] && load_ok="yes"
fi

# --- output isolation (build only) -------------------------------------------
iso_lines=""
if [ "$MODE" = "build" ]; then
  for d in .next/candidate-3100 .next/dev-3099; do
    if [ -f "$ROOT/frontend/$d/BUILD_ID" ]; then
      iso_lines="$iso_lines    $d: present, BUILD_ID at $(date -r "$ROOT/frontend/$d/BUILD_ID" -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || stat -c %y "$ROOT/frontend/$d/BUILD_ID" 2>/dev/null)
"
    elif [ -d "$ROOT/frontend/$d" ]; then
      iso_lines="$iso_lines    $d: present, no BUILD_ID
"
    else
      iso_lines="$iso_lines    $d: absent
"
    fi
  done
  if [ -d "$ROOT/frontend/.next/verify-build/.git" ]; then
    iso_lines="$iso_lines    .next/verify-build contains .git (refused)
"
    forced_fail="yes"
  fi
fi

# --- verdict -----------------------------------------------------------------
echo "Validation resource gate — mode: $MODE — $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "repo: $ROOT"
echo "cpu: $ncpu cpus, load1 ${load1:-?} (limit $((ncpu * LOAD_MAX_PCT_OF_NCPU / 100)).$((ncpu * LOAD_MAX_PCT_OF_NCPU % 100)) == ${LOAD_MAX_PCT_OF_NCPU}% of $ncpu cpus)"
echo ""
if [ -n "$disk_avail_kib" ]; then
  echo "disk available      : ${disk_avail_kib} KiB (${disk_mib} MiB)   threshold >= ${DISK_MIN_KIB} KiB   -> $disk_ok"
else
  echo "disk available      : unreadable                                 threshold >= ${DISK_MIN_KIB} KiB   -> $disk_ok"
fi
if [ -n "$ram_avail_mib" ]; then
  echo "RAM available       : ${ram_avail_mib} MiB   (wired ${wired_mib} MiB, compressor ${compressor_mib} MiB)"
  echo "  threshold         : >= ${RAM_MIN_MIB} MiB                                        -> $ram_ok"
else
  echo "RAM available       : unreadable                                 threshold >= ${RAM_MIN_MIB} MiB           -> $ram_ok"
fi
if [ -n "$swap_free_mib" ] && [ -n "$swap_total_mib" ]; then
  echo "swap free           : ${swap_free_mib} MiB of ${swap_total_mib} MiB   threshold >= ${SWAP_MIN_MIB} MiB -> $swap_ok"
else
  echo "swap free           : unreadable                                 threshold >= ${SWAP_MIN_MIB} MiB -> $swap_ok"
fi
echo "memory pressure     : ${pressure_pct:-?}% free                    threshold >= ${PRESSURE_MIN_PCT}%   -> $pressure_ok"
if [ -n "$iso_lines" ]; then
  echo "output isolation:"
  printf '%s' "$iso_lines"
fi
echo ""

for v in disk_ok ram_ok swap_ok pressure_ok load_ok; do
  eval "value=\$$v"
  case "$value" in
    yes) ;;
    unknown)
      unknown=1
      fail=1
      ;;
    *) fail=1 ;;
  esac
done
[ "$forced_fail" = "no" ] || fail=1

if [ "$fail" -eq 1 ]; then
  echo "VERDICT: BLOCKED — a required measurement is below threshold or unreadable."
  echo "Nothing heavy may start under this gate. Unreadable != safe."
  echo "These thresholds are a preliminary operational minimum, not proof of success."
  exit 3
fi

echo "VERDICT: ADMITTED (preliminary) — every dimension is above threshold."
echo "This is NOT proof the run will succeed or that the host will stay stable;"
echo "the runner that follows still verifies its own artifacts and stops on error."
exit 0