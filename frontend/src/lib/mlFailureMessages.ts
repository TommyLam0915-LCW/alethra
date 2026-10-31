// Translates the backend's known 400 responses into actionable Chinese.
//
// The thresholds below are copied from the checks in backend/app/services/ml_training.py
// and backend/app/ml/trainer.py (60 bars, 30 valid feature rows, a 10/5/5
// temporal split, a known date range, 30 bars for prediction). Anything not
// recognised returns null so the caller shows the backend's own text rather
// than guess at a requirement.
export function explainMlFailure(message: string): string | null {
  const bars = /Insufficient data for .*?: got (\d+) bars, need at least 60/i.exec(message);
  if (bars) {
    return `此標的只有 ${bars[1]} 根日線，ML 訓練至少需要 60 根。請先在「市場資料」載入更多歷史日線，或改選其他標的。`;
  }
  const rows = /Need at least 30 valid feature rows, got (\d+)/i.exec(message);
  if (rows) {
    return `可用特徵列只有 ${rows[1]} 筆，至少需要 30 筆。請拉長日期範圍或載入更多歷史資料。`;
  }
  const split = /Temporal split too small: train=(\d+), val=(\d+), test=(\d+)/i.exec(message);
  if (split) {
    return `時間切分後只有 ${split[1]} / ${split[2]} / ${split[3]} 筆（訓練 / 驗證 / 測試），最少需要 10 / 5 / 5。請增加歷史資料範圍。`;
  }
  if (/No known date range for/i.test(message)) {
    return '無法確定此標的的資料日期範圍（尚未儲存任何資料，資料來源也未提供範圍）。請先載入該標的的歷史資料。';
  }
  const predictionBars = /Need at least 30 bars for prediction, got (\d+)/i.exec(message);
  if (predictionBars) {
    return `以最新資料預測至少需要 30 根日線，目前只有 ${predictionBars[1]} 根。請先載入更多資料。`;
  }
  return null;
}
