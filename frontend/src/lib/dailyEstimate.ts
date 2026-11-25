// The daily model-estimate card's one three-way answer, kept out of the
// component so the wording can be tested without a browser.
//
// The status route hands the card three facts that used to look alike:
// an estimate this run produced (it carries the verdict and the market reading),
// an estimate the window had already been billed for (the bill proves the
// estimate exists and was paid for once, and holds no verdict), and no estimate
// at all (which the card renders as a refusal, not as this block). Folding the
// second into either neighbour is how a settled estimate starts reading as a
// failure — or as a blank where a verdict should be.

export interface EstimateRow {
  instrument_id: string;
  predicted_action?: string | null;
  /** Absent on a row the run itself produced; false on one read back off the
   *  billing ledger, which is where the no-verdict case comes from. */
  verdict_durable?: boolean;
  billed_at?: string | null;
  data_age_days?: number | null;
}

export interface EstimateDisplay {
  /** The verdict half of the title line, separator included. */
  verdict: string;
  /** True when this is the window's existing estimate, reused rather than bought
   *  again. */
  reused: boolean;
  /** The parenthetical that follows the data time point. */
  dataAge: string;
  /** The sentence shown only when ``reused``, so the card cannot state it for a
   *  fresh estimate. */
  reusedNote: string | null;
}

export function estimateDisplay(estimate: EstimateRow): EstimateDisplay {
  // The ledger row's own words, not an absence: a missing verdict on a row the
  // service says is not durable is a reused estimate, while a missing verdict
  // anywhere else stays what it looks like — this row does not hold one.
  const reused = estimate.verdict_durable === false;
  const dataAge =
    estimate.data_age_days != null
      ? `${estimate.data_age_days} 天前`
      : reused
        ? '資料 age 不在帳本列上'
        : '無法計算資料age';
  return {
    verdict:
      estimate.predicted_action == null
        ? '（此列未持有結論）'
        : ` · ${estimate.predicted_action}`,
    reused,
    dataAge,
    reusedNote: reused
      ? '沿用的是這個視窗已經存在的那一次估計，下面列的是它原本的 inference_id；' +
        '這次要求沒有再扣一次額度。' +
        (estimate.billed_at ? ` 計費時點 ${estimate.billed_at}。` : '') +
        '這既不是「今天沒有估計」也不是「本次被拒絕」：估計存在並已結算，' +
        '只是它的結論留在產生它的那次執行摘要裡，不在帳本列上。'
      : null,
  };
}

// ── the three states one skill account can be in, on one screen ─────────
//
// A blank used to be the answer to two different questions: 「這個視窗被拒絕了」
// and 「這個視窗還沒有任何人問過估計」. They are not the same fact — the first
// says the service ran and said no, the second says nothing ran — and the card
// has to say which one it means. ``reused`` is the third state the owner asked
// to keep apart: an estimate exists and is paid for, and this run inherited it
// rather than buying it again.
//
// ``unconfirmed`` is the fourth, and it is deliberately not called running. A
// window can hold a claim row (the tick opened), no billing row and no summary
// (the tick described nothing) at the same time. That is the whole of what the
// platform can prove, so the card says 執行狀態未確認: not 「仍在執行」, which
// would need a lease or heartbeat this lane does not write, and not 「已逾時／已死」,
// which would mean judging a process by how old its row is or by the word in its
// status column.

export type EstimateState =
  | 'carried_over'
  | 'estimated'
  | 'refused'
  | 'unconfirmed'
  | 'never_estimated';

export interface EstimateStateFacts {
  /** The card has an estimate row to show at all. */
  has_estimate: boolean;
  /** That row was rebuilt from the billing ledger, not produced by this run. */
  reused: boolean;
  /** The newest ledger row that names this skill refused it. */
  refused: boolean;
  /** A run opened this UTC window and left no bill, estimate or refusal behind. */
  window_claimed: boolean;
}

export function estimateState(facts: EstimateStateFacts): EstimateState {
  if (facts.has_estimate) return facts.reused ? 'carried_over' : 'estimated';
  if (facts.refused) return 'refused';
  return facts.window_claimed ? 'unconfirmed' : 'never_estimated';
}

export const ESTIMATE_STATE_LABELS_ZH: Record<EstimateState, string> = {
  carried_over: '已沿用',
  estimated: '本次估計',
  refused: '本次拒絕',
  unconfirmed: '執行狀態未確認',
  never_estimated: '沒有估計',
};

export interface FailureRow {
  skill_unit_id: string;
  instrument_id?: string;
  /** ``engine_failure`` — the estimate started and its worker raised. Anything
   *  this build does not name is printed as its code rather than as a sentence
   *  invented here. */
  reason: string;
  message_zh?: string;
  /** How many items this row named for this account. Absent is read as one. */
  count?: number;
  /** Only on a row written before the structured field existed: the account was
   *  parsed out of the run's error string rather than read off a field. */
  attributed_from?: string;
}

/** The saved failure as the card says it, and the only shape it may be said in.
 *
 *  The exception text stays in the job row and the log, so this function is
 *  written to have nothing to quote: it names the account, the item count and
 *  the instrument of the first one, and reads no other field of the row. Where
 *  the attribution came from a prefix rather than a field it says so, because
 *  「無法確認的歸屬」 and 「沒有發生命中該帳號的失敗」 are different answers and the
 *  card may not collapse them.
 */
export function failureNotice(failure: FailureRow): string {
  const parts = [
    `本次失敗（未落帳）：${failure.count == null ? 1 : failure.count} 筆`,
    failure.instrument_id ? `此處點名 ${failure.instrument_id}` : '此行未點名標的',
    failure.message_zh ? failure.message_zh : `原因代碼 ${failure.reason}`,
  ];
  if (failure.attributed_from === 'error_string_prefix') {
    parts.push('（這個帳號是從這次執行的錯誤記錄字首讀回的，不是欄位直接點名）');
  }
  parts.push(
    '這一列不帶平台內部的例外文字；這個視窗是否已扣額，看上方「最後估計」的 inference_id 與計費記錄，不看這條失敗。',
  );
  return parts.join(' · ');
}

/** Shown only in the ``never_estimated`` state, where the card was silent. */
export const NEVER_ESTIMATED_NOTE_ZH =
  '沒有估計：這個技能帳號至今沒有任何一次估計，也沒有拒絕紀錄可引用。' +
  '這不代表服務拒絕了你，也不是沿用了別天的估計——是這個視窗還沒有產生過任何估計。';

/** Shown only in the ``unconfirmed`` state, where a run opened and said nothing. */
export const UNCONFIRMED_NOTE_ZH =
  '執行狀態未確認：這個視窗有開工紀錄，但帳本沒有結算列、執行摘要也沒有估計或拒絕可引用。' +
  '平台只證明「有人開始過」，不證明它還在執行（這需要尚未實作的 lease／heartbeat 可靠信號），' +
  '也不會用這列存在多久、或狀態欄寫著什麼字樣來判定它已經停止。' +
  '這一頁不會因此退款、不會釋放預留、也不會把終態改寫成失敗。' +
  '同一視窗可以再要求一次：若其間已完成結算，重按只會沿用既有估計，不會再扣一次額度。';

// What the four things a hand press can bring back are named, and why the
// status code cannot name them. ``POST …/daily-estimate/run`` answers 201 for
// the attempt row it created — and an attempt whose estimate was refused,
// failed, or carried over from a bill already paid still creates that row. So
// the notice is read out of the job's own counts and the reason the ledger gave
// back, never off ``response.ok``: 2xx here means 「有這一次嘗試」, not 「有估計」.

export type PressOutcome = 'estimated' | 'carried_over' | 'partial' | 'nothing';

export interface PressFacts {
  /** ``job.estimated`` — estimates this press actually produced and billed. */
  estimated: number;
  /** ``job.refused`` — items a gate turned away, priced by nobody. */
  refused: number;
  /**
   * ``job.failed`` — items whose worker died after the gate opened. Priced
   * nothing, so they belong to 「部分成功」 exactly as refusals do; a ladder that
   * reads only ``refused`` reports ``1 estimated + 0 refused`` as a clean success
   * for a tick that killed one account's engine.
   */
  failed: number;
  /** The pressed account's own ``last_refusal.reason``, read back from status. */
  refusal_reason?: string | null;
}

export function pressOutcome(facts: PressFacts): PressOutcome {
  if (facts.estimated > 0) {
    return facts.refused > 0 || facts.failed > 0 ? 'partial' : 'estimated';
  }
  if (facts.refusal_reason === 'already_estimated_this_window') return 'carried_over';
  return 'nothing';
}

export const PRESS_NOTICES_ZH: Record<PressOutcome, string> = {
  estimated:
    '本次新增估計：帳本為這一次定價一筆，scope=manual。手動要求與每日排程共用同一個 UTC 視窗與同一筆帳本。',
  carried_over:
    '沿用：這個帳號在今天這個視窗已計價過，本次沒有再扣額度，並沿用既有那筆估計（其 inference_id 顯示在下方帳號卡）。',
  partial:
    '部分成功：只有產生估計的那幾筆被定價；被拒絕或失敗的項目沒有結算，逐項原因顯示在該帳號下方。',
  nothing:
    '全部失敗：本次沒有產生任何估計，也沒有新增結算、未扣額度。原因顯示在該帳號下方；這不是「已完成」。',
};

// True whatever the outcome was, so it rides on every press notice: the button
// does not open a second window, and the window it files into is billed once.
export const PRESS_WINDOW_NOTE_ZH =
  '手動要求與每日排程走同一個 UTC 視窗、同一筆帳本：同一視窗再按一次沿用既有那筆估計，不會多扣一次額度。';

// The one sentence that names what the HTTP response actually means. A 201 here
// is the create-attempt row, not an estimate, so it is kept next to the counts
// it is being confused with rather than in the component that reads both.
export function pressNotice(facts: PressFacts): string {
  return (
    PRESS_NOTICES_ZH[pressOutcome(facts)] +
    `（本次嘗試：新增 ${facts.estimated} 筆、拒絕 ${facts.refused} 項、失敗 ${facts.failed} 筆。` +
    '這個回應碼只代表這一次嘗試已建立，不代表產生了估計；以下方重新讀取的帳本為準。） ' +
    PRESS_WINDOW_NOTE_ZH
  );
}
// What a withdrawal does and does not reach. The lane honours the boundary the
// service actually enforces: the stop applies to the skill accounts whose first
// estimate has not started yet, and an estimate already in flight is spent when
// the owner withdraws. Saying "撤回即停止一切" would promise a refund the
// platform does not make.
export const WITHDRAWAL_TIMING_ZH =
  '撤回時機：撤回只對「尚未開始估計」的技能帳號生效。已經在執行中的那一次無法回收——' +
  '它照原樣結算一筆，並在帳本留下它已付費的事實；撤回之後的排程與手動要求都不再為這個帳號定價。' +
  '已計費的估計不會因撤回而退額，也不會被改寫。';

// Who is allowed to ask. Granting is the owner's own legal act, so the route
// refuses an API credential rather than record it as the owner's instruction;
// withdrawal is the safety action and stays reachable with the owner's own
// session (an agent key may stop the clock, and still may not start it).
export const CONSENT_PROVENANCE_ZH =
  '同意只能由本人以密碼登入的工作階段作出：API 金鑰（代理）提出每日估計同意會被拒絕，' +
  '不會被當成本人的指示記錄。撤回是停止動作的控制，因此保留給任何本人憑證可達，' +
  '但代理不得替本人同意。';

// ── one estimate row used as a manual decision's declared origin ────────
//
// The replay page's manual decision may carry ``linked_inference_id``: the
// estimate the owner is acting on. Two things make this a decision the page has
// to state rather than a field it can prefill. The route refuses the value on a
// ``hold`` (a hold on this door records no paper decision row, so the lineage
// would live only in a side blob), and the route verifies the row itself —
// same owner, personal-model prediction, same skill account. So the page can
// offer only the estimate its own bound skill account produced, and can send it
// only when the owner selects it for a buy or a sell.

export type ManualAction = 'buy' | 'sell' | 'hold';

export interface LineageFacts {
  /** The bound skill account has an estimate the status route can name. */
  has_source: boolean;
  /** The owner ticked the box this press. */
  selected: boolean;
  action: ManualAction;
}

export type LineageState =
  | 'attached'
  | 'unselected'
  | 'no_source'
  | 'hold_no_lineage';

export function lineageState(facts: LineageFacts): LineageState {
  if (facts.action === 'hold') return 'hold_no_lineage';
  if (!facts.has_source) return 'no_source';
  return facts.selected ? 'attached' : 'unselected';
}

export const LINEAGE_STATE_LABELS_ZH: Record<LineageState, string> = {
  attached: '會記錄來源推論',
  unselected: '不記錄來源（此列不會被寫進決策）',
  no_source: '這個技能帳號沒有可選的估計',
  hold_no_lineage: '觀望不攜帶來源',
};

/** The sentence a ``hold`` press must show instead of a checkbox. */
export const LINEAGE_HOLD_NOTE_ZH =
  '這一欄只在買進／賣出時送出：以這個入口作出的「觀望」不會建立紙上決策列，' +
  '把來源推論塞在旁邊的 blob 裡會讓兩個地方各自宣稱擁有同一份譜系，' +
  '所以後端會拒絕（reason：inference_needs_decision_row）。' +
  '要記錄「我看了這次估計決定觀望」，走技能帳號的決策歷史入口 ' +
  'POST /paper-portfolio/{portfolio_id}/decisions。';

/** What an attached source is *not* a proof of, next to the checkbox. */
export const LINEAGE_ATTACH_NOTE_ZH =
  '選取後這個入口會核對三件事：同一本人、個人模型決策推論、同一技能帳號；' +
  '不符就整筆拒絕，不會先扣額再回報失敗。這代表決策引用了哪一列估計，' +
  '不代表該推論在此刻仍有效或仍與目前選定的模型版本一致。';

// The checkbox question and the "no source" sentence live here, not in either
// door, because both doors submit through the same route: the day one of them
// rewords the offer, the other would still be asking the old question about a
// field that means what the first door said it means.
export const LINEAGE_ATTACH_LABEL_ZH = '以這個技能帳號最近的一次每日估計作為本次決策的來源';

/** Why an unreadable status is not rendered as an absent estimate. */
export const LINEAGE_NO_SOURCE_NOTE_ZH =
  '沒有可選的來源：每日估計狀態讀不到這個技能帳號的任何一次估計（或讀取失敗，讀取失敗不等於沒有估計）。' +
  '本頁不提供手動輸入 inference_id——頁面無法判斷你輸入的 id 是否屬於你本人、你的這個技能帳號。';

// The refusals `validate_linked_inference` answers, plus the hold case the
// simulations route rejects before it. Every manual decision door sends this
// field to the same validator, so the list lives here rather than in one page:
// a reason the server adds has to reach all of them at once, because a door
// that stops recognising a refusal goes on offering 「Retry (same payload)」
// under a 400 that can never pass.
export const LINEAGE_REFUSAL_REASONS: ReadonlySet<string> = new Set([
  'inference_not_found',
  'inference_metadata_unreadable',
  'inference_not_model_execution',
  'inference_skill_mismatch',
  'inference_needs_decision_row',
  'inference_version_mismatch',
  'inference_version_unnamed',
]);

// The version leg of `check_inference_version` answers in English, and the two
// refusals it raises read alike to a customer while asking for opposite
// disposals: one names a version that is not this decision's, the other names
// none at all (which is what a row written before that column existed looks
// like). An owner who cannot tell them apart either re-attaches the same source
// and gets the same refusal, or detaches a lineage that was never the problem.
// So each reason gets its own sentence here, where both doors read it.
export const LINEAGE_REFUSAL_LABELS_ZH: Readonly<Record<string, string>> = {
  inference_version_mismatch:
    '這個來源出自另一個模型版本的推論，與本次決策選定的版本不同，因此整筆拒絕、沒有寫入任何紀錄。' +
    '重送同一份表單仍是同一個版本差：請改選目前版本產生的估計，或取消勾選來源後再送一次。',
  inference_version_unnamed:
    '這個來源沒有記錄它出自哪個模型版本（或該欄讀不出可辨識的版本），因此無法核對，整筆拒絕、沒有寫入任何紀錄。' +
    '重送同一份表單不會讓它變得可核對：請改選有記錄版本的估計，或取消勾選來源後再送一次。',
};

export interface LineageRefusal {
  reason: string;
  message: string;
  /** The shared owner-language sentence for this reason, only when one exists. */
  message_zh?: string;
}

/**
 * Whether a failed request is one of those refusals, from its status and detail.
 *
 * The validator answers ``inference_not_found`` with 404 and the rest with 400,
 * so the reason names the class and the status only has to say "the request was
 * rejected": a page that matched on 400 alone would treat the 404 as a transient
 * failure and offer a retry that is refused again for the same reason.
 */
export function lineageRefusalOf(
  status: number | undefined,
  detail: unknown
): LineageRefusal | null {
  if (typeof status !== 'number' || status < 400 || status >= 500) return null;
  const reason = (detail as { reason?: unknown } | null)?.reason;
  if (typeof reason !== 'string' || !LINEAGE_REFUSAL_REASONS.has(reason)) return null;
  const message = (detail as { message?: unknown } | null)?.message;
  const refusal: LineageRefusal = {
    reason,
    message: typeof message === 'string' ? message : reason,
  };
  // A reason this module has no sentence for keeps the door's original wording
  // (the server's own message plus its reason code) rather than a generic one.
  const label = LINEAGE_REFUSAL_LABELS_ZH[reason];
  if (label) refusal.message_zh = label;
  return refusal;
}

// The status route's own words about the bound skill account's last estimate.
// A row read back off the billing ledger carries the inference id but no
// verdict, which is why ``predicted_action`` is rendered only when present.
export interface EstimateSource {
  inference_id: string;
  instrument_id: string | null;
  as_of_date: string | null;
  predicted_action?: string | null;
  verdict_durable?: boolean;
}

/**
 * The one estimate a manual decision may claim as its origin, or null.
 *
 * A status route that cannot be read is not evidence that no estimate exists,
 * so callers render "no source" only from this function's null and never from a
 * failed request; either way nothing is prefilled and nothing is auto-attached.
 */
export function estimateSourceFromStatus(status: any): EstimateSource | null {
  const last = status?.last_estimate;
  if (!last || !last.inference_id) return null;
  return {
    inference_id: String(last.inference_id),
    instrument_id: last.instrument_id ?? null,
    as_of_date: last.as_of_date ?? null,
    predicted_action: last.predicted_action ?? null,
    verdict_durable: last.verdict_durable,
  };
}

