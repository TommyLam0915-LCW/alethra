# Model Card Standard

**版本**: 1.0.0
**日期**: 2026-10-11

---

## 1. 概述

模型卡（Model Card）是每個研究模型的結構化文件，記錄模型的用途、限制、風險與免責聲明。其目的是讓任何查閱模型的人都能快速理解模型能做什麼、不能做什麼、以及使用時需要注意的事項。

### 設計原則

1. **必填欄位不可省略** — `disclaimer`、`excluded_use`、`known_limitations` 為必填，確保每個模型卡都有基本風險揭露
2. **所有權驗證** — 只有模型版本的擁有者才能建立或更新模型卡
3. **不可作為交易依據** — 模型卡是研究文件，不是交易建議

---

## 2. 模型卡結構

### 2.1 必填欄位

| 欄位 | 類型 | 說明 |
|------|------|------|
| `model_version_id` | UUID | 關聯的模型版本 |
| `purpose` | text | 模型卡的目的 |
| `intended_research_use` | text | 預期的研究用途 |
| `excluded_use` | text | **明確排除的使用場景** |
| `data_sources` | JSON | 資料來源描述 |
| `data_cutoff` | string (YYYY-MM-DD) | 資料截止日期 |
| `known_limitations` | JSON array | **已知限制列表** |
| `revocation_process` | text | 撤銷流程說明 |
| `disclaimer` | text | **免責聲明** |

### 2.2 選填欄位

| 欄位 | 類型 | 說明 |
|------|------|------|
| `linked_experiments` | JSON | 關聯的實驗 ID 列表 |
| `evaluation_evidence` | JSON | 評估證據摘要 |
| `risk_flags` | JSON | 風險旗標列表 |
| `version_history` | JSON | 版本歷史 |

---

## 3. 免責聲明要求

每個模型卡必須包含以下免責聲明（或語意等值的文字）：

```
此模型卡僅供研究參考。模型結果不代表未來績效，不構成投資建議。
模型不執行任何真實交易，不提供買賣信號。
使用此模型進行任何投資決策的風險由使用者自行承擔。
```

---

## 4. 排除使用場景

`excluded_use` 欄位必須明確列出模型不應該被使用的場景，例如：

- 不應用於真實資金交易決策
- 不應用於高頻交易或日內交易
- 不應用於未經充分理解的資產類別
- 不應用於槓桿或保證金交易

---

## 5. 已知限制

`known_limitations` 欄位應列出所有已知的限制，包括但不限於：

- 資料限制（時間範圍、資產類別、頻率）
- 方法論限制（假設、近似、模型風險）
- 執行限制（滑價、流動性、市場衝擊）
- 驗證限制（回測過擬合風險、樣本外表現不確定性）

---

## 6. 風險旗標

`risk_flags` 欄位可用於標記特殊風險，例如：

```json
[
  {
    "flag": "overfitting_risk",
    "severity": "medium",
    "description": "模型參數較多，存在過擬合風險"
  },
  {
    "flag": "data_snooping",
    "severity": "low",
    "description": "模型開發過程中曾檢視完整資料集"
  }
]
```

---

## 7. API 使用

### 建立模型卡

```
POST /api/v1/model-cards
Authorization: Bearer <token>

{
  "model_version_id": "<version-uuid>",
  "purpose": "記錄 SMA 交叉策略的研究結果",
  "intended_research_use": "理解均線交叉在不同市場環境下的表現",
  "excluded_use": "不用於真實交易、不用於高頻策略",
  "data_sources": {"demo_assets": ["DEMO-ALPHA"]},
  "data_cutoff": "2024-02-14",
  "known_limitations": ["僅使用示範資料", "未考慮交易成本", "樣本數有限"],
  "revocation_process": "若發現方法論錯誤，立即撤銷並記錄原因",
  "disclaimer": "此模型卡僅供研究參考，不代表未來績效，不構成投資建議。"
}
```

### 更新模型卡

```
PATCH /api/v1/model-cards/{id}
Authorization: Bearer <token>

{
  "risk_flags": [{"flag": "new_risk", "severity": "low", "description": "..."}]
}
```

---

## 8. 與其他階段的關係

- **Phase 1** — 實驗記錄提供回測數據，可被模型卡引用
- **Phase 3A** — 前瞻模擬提供 forward observation 證據
- **Phase 3B** — Paper portfolio 提供決策行為追蹤數據
- **Phase 4** — 模型卡整合所有證據，形成完整的研究文件
