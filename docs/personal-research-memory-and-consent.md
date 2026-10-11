# Personal Research Memory and Consent

**版本**: 1.0.0
**日期**: 2026-10-11

---

## 1. 概述

個人研究記憶系統讓用戶能夠記錄研究過程中的偏好、約束、反思和方法論問題。所有記憶都必須經過用戶**明確確認**才會生效，系統不會在未經確認的情況下推斷或套用任何偏好。

### 核心原則

1. **明確同意** — 記憶必須經用戶明確確認（confirm）才會生效
2. **不推斷偏好** — 系統不會從行為或上下文推斷隱藏偏好
3. **可撤銷** — 用戶可隨時停用任何記憶
4. **隱私預設** — 所有記憶預設為 `private_only`
5. **訓練選填** — 一般模型訓練選填（general_model_training_opt_in）預設為 false，且只能在記憶確認後由用戶手動啟用

---

## 2. 記憶類型

| 類型 | 說明 |
|------|------|
| `explicit_preference` | 用戶明確表達的研究偏好 |
| `approved_constraint` | 用戶已核准的研究約束 |
| `recurring_methodology_issue` | 反覆出現的方法論問題 |
| `reflection` | 研究過程中的反思 |
| `accepted_ai_draft` | 已接受的 AI 草稿建議 |
| `rejected_ai_draft` | 已拒絕的 AI 草稿建議 |

---

## 3. 隱私範圍

| 範圍 | 說明 |
|------|------|
| `private_only` | 僅私人，不對外分享（預設） |
| `approved_for_personal_ai` | 允許個人 AI 工具使用 |

隱私範圍可在建立時設定，也可在後續更新。變更隱私範圍不影響確認狀態。

---

## 4. 同意流程

### 4.1 建立記憶

建立記憶時可選擇是否立即確認：

```json
{
  "memory_type": "explicit_preference",
  "content": "我偏好使用 SMA(20,50) 交叉作為進場信號",
  "privacy_scope": "private_only",
  "user_confirmed": false
}
```

若 `user_confirmed: false`（預設），記憶狀態為「待確認」，不會被套用到任何研究流程。

### 4.2 確認記憶

用戶必須明確確認或拒絕：

```json
POST /api/v1/personal-memory/{id}/confirm
{
  "confirmed": true
}
```

確認後，`user_confirmed_at` 時間戳被設定，記憶正式生效。

### 4.3 拒絕記憶

```json
POST /api/v1/personal-memory/{id}/confirm
{
  "confirmed": false
}
```

拒絕後記憶保持未確認狀態，不會生效。

### 4.4 停用記憶

```
DELETE /api/v1/personal-memory/{id}
```

停用後 `active` 設為 false，記憶不再被套用。停用是可逆的（未來可重新啟用）。

---

## 5. 一般模型訓練選填

`general_model_training_opt_in` 控制是否允許將已確認的記憶用於一般模型訓練。

### 限制

1. **預設為 false** — 建立記憶時不可設為 true
2. **API 不可直接設為 true** — 嘗試透過 API 建立時設為 true 會返回 422 錯誤
3. **必須先確認記憶** — 只有已確認的記憶才能變更此設定
4. **用戶手動控制** — 此設定只能由用戶在 UI 上手動切換

### 變更流程

```
PUT /api/v1/personal-memory/{id}/training-opt-in?opt_in=true
```

若記憶尚未確認，返回 400 錯誤。

---

## 6. API 端點

| 方法 | 路徑 | 說明 |
|------|------|------|
| POST | `/api/v1/personal-memory` | 建立記憶 |
| GET | `/api/v1/personal-memory` | 列出記憶（可篩選 memory_type） |
| GET | `/api/v1/personal-memory/{id}` | 取得單一記憶 |
| POST | `/api/v1/personal-memory/{id}/confirm` | 確認或拒絕記憶 |
| DELETE | `/api/v1/personal-memory/{id}` | 停用記憶 |
| PUT | `/api/v1/personal-memory/{id}/training-opt-in` | 變更訓練選填設定 |

---

## 7. 前端頁面

路徑：`/personal-memory`

功能：
- 列出所有記憶，支援類型篩選
- 建立新記憶表單（類型、內容、隱私範圍、是否立即確認）
- 確認/拒絕按鈕（僅未確認記憶顯示）
- 停用按鈕
- 模型訓練設定（僅已確認記憶顯示）

---

## 8. 安全與隱私

1. **所有權隔離** — 用戶只能存取自己的記憶
2. **預設隱私** — 新記憶預設 `private_only`
3. **不可批量匯出** — 無提供批量匯出 API
4. **無第三方分享** — 記憶不會被分享給任何第三方
5. **事件記錄** — 所有建立、確認、停用操作都會記錄在事件日誌中

---

## 9. 與其他功能的關係

- **模型研究記錄** — 記憶可關聯到特定的模型記錄（透過 `source_entity_type` 和 `source_entity_id`）
- **模型卡** — 已確認的記憶可作為模型卡的證據來源
- **實驗系統** — 記憶不直接影響實驗執行，僅作為研究過程的參考
