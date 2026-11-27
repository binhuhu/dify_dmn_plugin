#!/bin/bash
# 全链路无影响闸照已认差异清单出结论：先跑闸（run_gate.sh，在本仓外，老树对新树逐字节对跑现网全部调用形状），
# 再由 no-impact-verdict.py 照随版提交的已认差异清单算结论。结论是脚本算的，不由人看着判。
# 用法：no-impact-gate.sh <run_gate.sh> <老树> <新树> <结果目录（不存在或为空）> <已认差异清单>
# 退出码：0 绿（每一处差异都在清单上、清单上每一条都出现了）；1 红；2 用法、输入错或闸没跑完
set -u
GATE="${1:?run_gate.sh}"; OLD="${2:?老树}"; NEW="${3:?新树}"; OUT="${4:?结果目录}"; LIST="${5:?已认差异清单}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${VERDICT_PY:-python3}"
[ -f "${GATE}" ] || { echo "闸脚本不在：${GATE}"; exit 2; }
[ -f "${LIST}" ] || { echo "已认差异清单不在：${LIST}"; exit 2; }
if [ -e "${OUT}" ] && [ -n "$(ls -A "${OUT}" 2>/dev/null)" ]; then
  echo "结果目录不空（上一趟的读数会混进来）：${OUT}"; exit 2
fi
bash "${GATE}" "${OLD}" "${NEW}" "${OUT}"
RAW=$?
mkdir -p "${OUT}"
echo "${RAW}" > "${OUT}/闸原始退出码.txt"
# 闸本身：0 全同、1 有差异（照清单判）；别的是用法或输入错，或没跑完
if [ "${RAW}" != 0 ] && [ "${RAW}" != 1 ]; then echo "闸没跑完（退出码 ${RAW}）"; exit 2; fi
"${PY}" -B "${HERE}/no-impact-verdict.py" "${OUT}" --accepted "${LIST}" --out "${OUT}/结论.json"
RC=$?
case "${RC}" in
  0) echo "闸照清单：绿" ;;
  1) echo "闸照清单：红" ;;
  *) echo "闸照清单：输入错（退出码 ${RC}）" ;;
esac
exit "${RC}"
