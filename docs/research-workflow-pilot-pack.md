# Alethra Research-Workflow Pilot Pack

**Date:** 2026-10-15  
**Status:** Prepared for owner review  
**Reference commit:** `6a254e3853778492d359bffce68a31ac1e2d1600` (main)

---

## 1. Purpose

This pilot tests two distinct questions:

**Question 1: Demo usability**
Can people with existing research habits use the current Alethra product to organise one research example?

**Question 2: Research-workflow value**
Does organising research in Alethra address a real pain they experience in their actual workflow?

The core hypothesis:

> People with existing investment-research habits may pay to reduce research organisation work and more easily compare assumptions, strategy changes and subsequent results.

This hypothesis is **not yet validated**. No users have been contacted. No retention, conversion or payment results exist.

This pack provides materials for a small pilot (3-5 participants) to gather directional evidence about whether the hypothesis warrants further development.

**This is not a market-size estimate. This is not a commercial launch.**

**Important distinction:** A participant who can complete the demo task (usability) is not evidence that the product addresses their real workflow pain (value). Both must be observed separately.

---

## 2. Current Product State

**What exists (offline/demo/test-mode):**
- Experiment creation with hypothesis, falsification criteria, original idea
- Strategy version tracking (version number, change reason, timestamp)
- Backtest execution using historical demo data (not real market data)
- Method review panel showing hypothesis/strategy/falsification text
- Timeline of research events (created, versioned, backtested)
- Forward simulation (locked strategy, snapshot release)
- Dashboard with research-focused navigation
- All data stored locally; no external integrations

**What does not exist:**
- Real market data (demo provider only)
- Cross-experiment comparison
- Decision record (why user changed strategy beyond version change_reason)
- Report export for research records
- Notifications or reminders
- AI summaries or suggestions
- Public sharing or collaboration
- Payment processing or subscription management (test billing only)

**Migration status:**
- Fresh PostgreSQL installation works
- Existing `create_all()` databases cannot migrate (Scenario B blocked)
- No operational databases affected

---

## 3. Participant Screening (Section A)

### Target participant profile

Seek people who:

- [ ] Have a real strategy or research method they have used for ≥3 months (heuristic, not proven threshold — revise if pilot reveals this excludes relevant participants or includes unsuitable ones)
- [ ] Review results periodically (weekly, monthly, quarterly)
- [ ] Use spreadsheets, notes, or multiple tools to organise research
- [ ] Can describe a recent concrete organisation or comparison problem
- [ ] Are not beginners seeking guidance (this is not an educational tool)
- [ ] Are not professional teams with established infrastructure

**Note:** The ≥3 months heuristic is an assumption about what constitutes "existing research habit." If pilot observations reveal that participants with <3 months experience also have valid research workflows, or that participants with >3 months do not, this threshold should be revised.

### Exclusion criteria

Do not recruit:
- People with no existing research habit
- People seeking trade signals or stock picks
- Professional fund managers with compliance requirements
- People expecting real-time data or live trading

### Recruitment channels (owner decision required)

- Personal network (friends, colleagues, former classmates)
- Investment research communities (forums, Discord, Reddit)
- University alumni groups (finance, economics, data science)
- Local meetups (if permitted by owner)

**Do not contact anyone without owner approval.**

---

## 4. Non-Leading Interview Questions (Section B)

Ask about **recent actual work**, not hypothetical interest in Alethra.

### Opening (5 minutes)

1. "Tell me about the last strategy or research method you changed. What was it?"
2. "How did you record the reason for that change?"
3. "Where did you look to see what you had assumed before?"

### Deep dive (15 minutes)

4. "Walk me through how you compared the new results to what you expected."
5. "What tools or files did you open during that process?"
6. "Was anything difficult to find or had to be repeated?"
7. "If you had to do that same comparison again in 3 months, how would you do it?"

### Organisation pain (10 minutes)

8. "Show me how you currently organise your research notes or spreadsheets."
9. "What happens when you want to see the history of a particular idea?"
10. "Have you ever lost track of why you made a change? What happened?"

### Avoid leading questions

Do not ask:
- "Would you use a tool that...?" (hypothetical)
- "Don't you find it frustrating when...?" (leading)
- "How much would you pay for...?" (premature)

Instead ask:
- "How do you currently handle...?" (actual behaviour)
- "What was difficult about that?" (observed pain)
- "What alternatives have you tried?" (existing solutions)

---

## 5. First-Session Task (Section C)

### Preparation

Ask the participant to bring **one real research example**:
- A strategy or method they have been testing or reviewing
- Access to their current notes, spreadsheets, or tools (if willing to share)
- ~60 minutes available

**Optional: Participant-owned research example (redacted)**

If the participant is willing to share their actual research, they may use it. However:

**Do NOT share:**
- Broker credentials or account access
- Specific holdings, position sizes, or portfolio values
- Personal financial information or net worth
- Proprietary or confidential research from employers
- Any data that could identify specific trades or investment decisions

**May share (with redaction if needed):**
- Strategy logic or rules (e.g., "I buy when SMA(20) crosses above SMA(50)")
- Hypothetical examples based on real methods
- Redacted notes or spreadsheets (remove specific tickers, amounts, dates if sensitive)
- General research process (how they organise ideas, not specific outcomes)
- Public information or educational examples

**Moderator guidance:**

- Ask: "Are you comfortable sharing this example, or would you prefer to use a hypothetical one?"
- If sharing: "Please remove any sensitive information like specific holdings, account values, or proprietary data."
- If uncertain: "You can describe the strategy logic without sharing specific tickers or amounts."
- Always offer: "A hypothetical example based on your real method is perfectly fine."

**The goal is to observe their research workflow, not to collect their investment data.**

### Task instructions

"Using Alethra, I'd like you to:

1. Create an experiment for your research example.
2. Record your hypothesis, falsification criteria, and original idea.
3. Run a backtest (using demo data).
4. Look at the method review panel.
5. Check the timeline of events.
6. Explain what you would decide next, based on what you see."

### Observations

Record whether they can:

- [ ] Preserve the question/rules (hypothesis, falsification, original idea)
- [ ] Identify the relevant version (version number, change reason)
- [ ] Inspect available test evidence (backtest results, warnings, limitations)
- [ ] Record or explain the current decision using existing capabilities

### Critical distinctions

**Clearly state to the participant:**

- "The backtest uses historical demo data, not real market data."
- "The results are for demonstration only and do not represent future performance."
- "This is a research tool, not investment advice."
- "Your actual research evidence is whatever you brought with you, not what Alethra shows."

### Gap recording

If a required capability is absent:

- Do not build it during the session
- Record the gap: "Participant needed to [X] but could not because [Y]"
- Note whether the gap blocked the task or was workarounded

### Time budget

- Introduction and setup: 10 minutes
- Task execution: 30 minutes
- Debrief and questions: 20 minutes

---

## 6. Return-Session Task (Section D)

### Account and data retention

**Critical:** Participant accounts and research data must NOT be deleted between the first and return sessions.

- Do not delete the test user account after the first session
- Do not clear the database or reset participant data
- Preserve all experiments, versions, backtests, and timeline events
- Retain data until the pilot is complete and the participant has confirmed they no longer need access

**Data deletion timing (owner decision required):**
- When should participant data be deleted? (e.g., 1 week after final session, 1 month after pilot ends)
- Should participants be notified before deletion?
- Should participants be offered a data export before deletion?

**This decision is awaiting owner confirmation. Do not delete data until the owner approves a retention policy.**

### Timing and session types

**Scheduled return session (arranged):**

Schedule the return session after an appropriate interval:
- Minimum: 1 week (to allow real research to occur)
- Maximum: 1 month (to maintain continuity)

This is an **arranged follow-up**, not evidence of spontaneous reuse or retention.

**Spontaneous reuse (if it occurs):**

If a participant uses Alethra between sessions without being prompted:
- Record this as spontaneous reuse
- Ask: "Did you use Alethra since our last session? What did you do?"
- This is stronger evidence of value than an arranged return session

**Important distinction:**
- An arranged return session tests whether the participant *can* retrieve their reasoning (usability)
- Spontaneous reuse tests whether the participant *chooses* to use Alethra without prompting (value)
- Do not call an arranged second session "retention" or "adoption"

### Task instructions

"Revisit the same research example you worked on last time.

1. Retrieve your original reasoning in Alethra.
2. Note what has changed (if anything) since the last session.
3. Interpret any new observations using your own evidence.
4. Decide whether to retain, revise, stop, or wait for more evidence."

### Observations

Record whether they can:

- [ ] Retrieve original reasoning (hypothesis, falsification, version history)
- [ ] Understand what has changed (new version, new backtest, new timeline events)
- [ ] Interpret new observations (compare demo results to their actual evidence)
- [ ] Make a decision (retain, revise, stop, or wait)

### Success criteria

**A strategy change or trade is not required for success.**

Success includes:
- Participant retrieved their original reasoning
- Participant understood the current state
- Participant made a deliberate decision (even if the decision is "wait")
- Participant found the workflow useful for organising their thinking

Failure includes:
- Participant could not find their original reasoning
- Participant was confused about what changed
- Participant abandoned the task due to missing capabilities

---

## 7. Observation Template (Section E)

### Session metadata

- **Participant ID:** [anonymised, e.g., P01]
- **Session date:** [YYYY-MM-DD]
- **Session type:** [first / return]
- **Moderator:** [name]
- **Duration:** [minutes]

### Task completion

- [ ] Hypothesis recorded
- [ ] Falsification criteria recorded
- [ ] Original idea recorded
- [ ] Backtest executed (first session only)
- [ ] Method review inspected
- [ ] Timeline inspected
- [ ] Decision explained

### Time breakdown

| Task | Elapsed time | Context |
|------|--------------|---------|
| Setup | [min] | [e.g., "familiar with forms"] |
| Hypothesis entry | [min] | [e.g., "paused to think"] |
| Backtest execution | [min] | [e.g., "expected faster"] |
| Method review | [min] | [e.g., "read carefully"] |
| Decision explanation | [min] | [e.g., "clear reasoning"] |

### Moderator assistance protocol

**Do not simply "observe without intervening."** Use a consistent three-level assistance protocol:

**Level 1: Observe (no intervention)**
- Let the participant work independently
- Record what they do without prompting
- Note time spent, errors, confusion

**Level 2: Neutral prompt (if blocked for >2 minutes)**
- Offer a neutral, non-leading prompt:
  - "Take your time. Let me know if you have any questions."
  - "Is there anything on the page that might help?"
  - "What are you trying to do?"
- Do not suggest specific actions or buttons
- Record the prompt and participant response

**Level 3: Explicit help (only when necessary)**
- If the participant is still stuck after Level 2:
  - Ask: "Would you like me to show you where that is?"
  - If yes, provide explicit guidance
  - Record the exact help provided
- If no, continue observing

**Record every intervention:**

| Timestamp | Level | Prompt/Help provided | Participant response |
|-----------|-------|---------------------|---------------------|
| [mm:ss] | [1/2/3] | [exact words or "none"] | [what they did next] |

**Do not skip Level 2.** Always offer a neutral prompt before providing explicit help. This distinguishes usability issues (needs Level 3) from discoverability issues (needs Level 2).

### Repeated inputs

Note if the participant had to repeat any action:

- [ ] Re-entered hypothesis
- [ ] Re-ran backtest
- [ ] Re-read the same section
- [ ] Other: [describe]

### Errors and confusion

Record exact moments of confusion or error:

- **Timestamp:** [mm:ss]
- **What happened:** [describe]
- **Participant statement:** [quote with consent]
- **Resolution:** [self-corrected / moderator assistance / abandoned]

### Participant statements (with consent)

Record exact quotes that reveal insight:

- "..."
- "..."
- "..."

**Obtain explicit consent before recording quotes. Anonymise in reports.**

### Interest in another task

- [ ] Participant asked to try another research example
- [ ] Participant declined
- [ ] Participant suggested a different use case

### Reasons for returning or not returning

**If returning:**
- "..."

**If not returning:**
- "..."

### Observations vs. interpretations

**Observations (facts):**
- Participant completed the task in 28 minutes
- Participant asked for help twice
- Participant said, "I wish I could see all my versions side by side"

**Interpretations (moderator judgment):**
- Participant found the workflow intuitive
- Participant was confused by the distinction between hypothesis and falsification
- Participant valued version comparison

**Keep observations separate from interpretations.**

**Do not invent results. Do not calculate unsupported time savings.**

---

## 8. Commercial Exploration (Section F)

### Neutral questions

Ask about **current alternatives and circumstances**, not willingness to pay.

1. "What tools or methods do you currently use for this research?"
2. "How much time do you spend on this process (per week/month)?"
3. "What do you like about your current approach?"
4. "What do you dislike or find frustrating?"
5. "Have you tried other tools? What happened?"
6. "Under what circumstances would you consider adopting a new tool?"
7. "What would need to be true for you to pay for a research organisation tool?"
8. "What would make you reject it?"

### Do not

- Choose or activate pricing
- Equate stated willingness with actual payment evidence
- Ask "How much would you pay?" (hypothetical and unreliable)
- Promise features or discounts

### Interpret with caution

**Stated interest ≠ actual adoption.**

Directional evidence:
- Participant described a concrete, recent pain
- Participant identified a specific circumstance for adoption
- Participant asked when the tool would be available
- Participant offered to be contacted again

Weak evidence:
- Participant said "that's interesting" but gave no specifics
- Participant could not describe a recent pain
- Participant said "I'd pay for that" without explaining why

---

## 9. Decision Rules (Section G)

### Continue the hypothesis

**Directional evidence for continuing:**

- ≥3 of 5 participants completed both sessions
- ≥3 of 5 participants described a concrete, recent organisation pain
- ≥3 of 5 participants retrieved their original reasoning in the return session
- ≥2 of 5 participants asked to try another research example
- ≥2 of 5 participants identified a specific circumstance for adoption

### Narrow the audience or problem

**Evidence for narrowing:**

- Only a subset of participants (e.g., quantitative researchers, not discretionary) completed the task
- Only a subset of participants (e.g., those with ≥5 strategies) found the workflow useful
- Only a subset of participants (e.g., those using spreadsheets) described a concrete pain

**Proposed narrow focus:** [to be determined after pilot]

### Revise the workflow

**Evidence for revision:**

- ≥3 of 5 participants could not complete the task without significant moderator assistance
- ≥3 of 5 participants were confused by the distinction between hypothesis, falsification, and original idea
- ≥3 of 5 participants could not retrieve their original reasoning in the return session
- Participants consistently needed a capability that does not exist (e.g., cross-experiment comparison)

**Proposed revision:** [to be determined after pilot]

### Postpone further feature development

**Evidence for postponing:**

- <2 of 5 participants completed both sessions
- <2 of 5 participants described a concrete, recent organisation pain
- Participants could not distinguish demo results from their actual research evidence
- Participants showed no interest in returning or trying another example

**Proposed next step:** Revisit hypothesis after gathering more qualitative evidence from a different audience

---

## 10. Assumptions and Limitations

### Assumptions

1. Participants have an existing research habit (not beginners)
2. Participants can articulate their recent work (not hypothetical)
3. Participants are willing to share their research process (not proprietary)
4. Demo data is sufficient for a first-session task (not real market data)
5. One research example is representative (not exhaustive)

### Limitations

1. **Small sample size:** 3-5 participants is directional, not statistical
2. **Self-selection bias:** Participants who volunteer may be more motivated than average
3. **Moderator effect:** Moderator assistance may mask usability issues
4. **Demo data:** Participants cannot test with their own data (ethical and technical constraints)
5. **No longitudinal evidence:** Return session is one data point, not ongoing usage
6. **No payment evidence:** Stated interest ≠ actual adoption

### Current product limitations

1. **Demo data only:** Backtest results are not real market data
2. **No cross-experiment comparison:** Cannot compare multiple strategies side by side
3. **No decision record:** Cannot record why a decision was made beyond version change_reason
4. **No report export:** Cannot export research records for external use
5. **No notifications:** Cannot remind participants to revisit their research
6. **No AI summaries:** Cannot generate insights or suggestions
7. **No public sharing:** Cannot collaborate with others
8. **Scenario B blocked:** Existing databases cannot migrate (not relevant for new pilot participants)

---

## 11. Owner Decisions Required

Before recruiting or hosting sessions, the owner must decide:

1. **Recruitment channels:** Which channels are approved for contacting potential participants?
2. **Participant compensation:** Will participants be compensated for their time? If so, how much?
3. **Moderator identity:** Who will moderate the sessions (owner, delegate, or external facilitator)?
4. **Session format:** In-person, video call, or screen-sharing with narration?
5. **Pilot timeline:** When will the pilot start and end?
6. **Success criteria:** Are the decision rules in Section 9 acceptable, or should they be revised?
7. **Feature gaps:** If gaps are observed, should they be addressed before, during, or after the pilot?
8. **Go/no-go threshold:** How many positive signals are needed to continue development?

### Data and consent decisions (awaiting owner confirmation)

**These decisions have NOT been made. Do not proceed until the owner confirms:**

9. **Data retention period:**
   - How long will participant data be retained after the pilot?
   - When should data be deleted? (e.g., 1 week, 1 month, 3 months after final session)
   - Should participants be notified before deletion?
   - Should participants be offered a data export before deletion?

10. **Recording permission:**
    - Will sessions be audio/video recorded?
    - Will screen recordings be made?
    - Will notes be taken during the session?
    - What happens to recordings after the pilot?

11. **Access policy:**
    - Who will have access to participant data and recordings?
    - Will data be stored locally or in the cloud?
    - Will data be anonymised in reports?
    - Will raw data be deleted after analysis?

**This is not legal advice. This is not a legal compliance certification.**

**The owner must decide these policies based on their own legal requirements and risk tolerance. If legal review is needed, consult a qualified professional.**

**Do not proceed with the pilot until these decisions are made and documented.**

---

## 12. Demonstration Checklist

For safely demonstrating the existing system:

### Pre-session preparation

- [ ] Local PostgreSQL database running (fresh installation)
- [ ] Backend server running on `localhost:8001`
- [ ] Frontend server running on `localhost:3001`
- [ ] Test user account created (disposable, not production)
- [ ] Demo data provider confirmed (no real market data)
- [ ] Disclaimer visible on landing page ("Research tool, not investment advice")
- [ ] Participant consent obtained (verbal or written)

### During session

- [ ] Clearly state: "This uses demo data, not real market data"
- [ ] Clearly state: "Results are for demonstration only"
- [ ] Clearly state: "This is a research tool, not investment advice"
- [ ] Observe without intervening (unless participant is stuck)
- [ ] Record observations using the template in Section 7
- [ ] Record exact participant statements with consent
- [ ] Note gaps without building features

### Post-session

- [ ] Anonymise participant data
- [ ] Delete test user account (if disposable)
- [ ] Synthesize observations (separate from interpretations)
- [ ] Compare to decision rules in Section 9
- [ ] Report to owner with recommendations

---

## 13. Proposed Features (Requiring Owner Approval)

**Only list features if they address observed or clearly identified task gaps.**

**Do not implement these before the pilot. Do not implement during the pilot.**

**After the pilot, if gaps are observed, propose:**

1. **Cross-experiment comparison** (if participants need to compare multiple strategies)
   - Status: Not implemented
   - Requires: Owner approval, design, implementation, testing
   - Estimated effort: 2-3 days

2. **Decision record** (if participants need to record why a decision was made)
   - Status: Not implemented
   - Requires: Owner approval, design, implementation, testing
   - Estimated effort: 1-2 days

3. **Report export** (if participants need to export research records)
   - Status: Not implemented
   - Requires: Owner approval, design, implementation, testing
   - Estimated effort: 1-2 days

4. **Notifications or reminders** (if participants forget to revisit their research)
   - Status: Not implemented
   - Requires: Owner approval, design, implementation, testing
   - Estimated effort: 1-2 days

5. **AI summaries or suggestions** (if participants need help interpreting results)
   - Status: Not implemented
   - Requires: Owner approval, design, implementation, testing
   - Estimated effort: 3-5 days

**Do not implement any of these without owner approval and observed evidence.**

---

## 14. Invitation Draft (Not Sent)

**This is a draft invitation. Do not send without owner approval.**

---

**Subject:** Invitation to participate in a research-workflow pilot study

Hi [Name],

I'm working on a tool called Alethra that helps people organise their investment research. I'm looking for a small number of people with existing research habits to try it out and give feedback.

**What it involves:**
- Two sessions, each about 60 minutes
- First session: Use Alethra to organise one of your research examples (you can use a hypothetical example if you prefer)
- Return session (1-4 weeks later): Revisit the same research and see how the workflow feels
- I'll observe how you use the tool and ask questions about your research process

**What it's not:**
- This is not investment advice
- The tool uses demo data, not real market data
- I won't ask for your broker credentials, holdings, or personal financial information
- You can use a redacted or hypothetical example if you're more comfortable

**What you get:**
- [Compensation details if approved by owner, e.g., "$50 gift card for your time"]
- Early access to a research organisation tool
- Your feedback will directly shape the product

**What I need from you:**
- Consent to be observed and have notes taken during the session
- [If approved: Consent to audio/video recording]
- Willingness to share your research process (not specific trades or holdings)

If you're interested, I'd love to chat for 15 minutes to see if this is a good fit.

Thanks,
[Your name]

---

**Do not send this invitation without owner approval.**

---

## 15. Consent Draft (Plain Language, Not Legal Certification)

**This is a draft consent form. This is not legal advice or a legal compliance certification.**

**The owner must review and approve this consent form before use. If legal review is needed, consult a qualified professional.**

---

**Alethra Research-Workflow Pilot — Participant Consent**

**What is this?**
You're being asked to participate in a pilot study for a research organisation tool called Alethra. The goal is to learn whether the tool addresses real research-workflow pain.

**What will you do?**
- Attend two sessions (each ~60 minutes), scheduled 1-4 weeks apart
- Use Alethra to organise one research example (real or hypothetical)
- Answer questions about your research process
- Revisit your research in the second session

**What will be recorded?**
- [ ] Notes taken during the session (moderator will write down what you do and say)
- [ ] Audio recording of the session
- [ ] Video recording of the session (if video call)
- [ ] Screen recording of your interaction with Alethra

**Check all that apply (owner to confirm before session):**

**How will recordings be used?**
- Recordings will be used to analyze the pilot results
- Recordings will be anonymised in reports (your name will not appear)
- Recordings will be deleted [X weeks/months] after the pilot ends
- [Owner to specify retention period]

**What about your data?**
- Your Alethra account and research data will be retained between sessions
- Your data will be deleted [X weeks/months] after the pilot ends
- You can request early deletion at any time
- [Owner to specify retention period]

**What about your privacy?**
- You do not need to share broker credentials, holdings, or personal financial information
- You can use a hypothetical or redacted example
- Your specific trades and investment decisions will not be recorded
- Your participation is confidential

**Can you withdraw?**
- Yes, you can stop participating at any time
- You can request that your data be deleted
- There is no penalty for withdrawing

**What are the risks?**
- Minimal risk: This is a research tool, not investment advice
- The tool uses demo data, not real market data
- You may spend time on a tool that doesn't address your needs

**What are the benefits?**
- [Compensation if approved by owner]
- Early access to a research organisation tool
- Your feedback will shape the product

**Questions?**
Contact [owner/moderator name] at [email/contact method].

**Consent:**

By signing below (or verbally agreeing), you confirm that:
- You understand what the pilot involves
- You agree to be [observed / recorded — check applicable]
- You understand your data will be retained until [date/period] and then deleted
- You can withdraw at any time

Participant name: _____________________

Participant signature: _____________________

Date: _____________________

Moderator name: _____________________

Moderator signature: _____________________

Date: _____________________

---

**This is not legal advice. The owner must review and approve this consent form before use.**

---

## 16. Moderator Checklist

### Before the session

- [ ] Owner has approved recruitment, compensation, and session format
- [ ] Owner has approved data retention period, recording permission, and access policy
- [ ] Consent form reviewed and approved by owner
- [ ] Local PostgreSQL database running (fresh installation)
- [ ] Backend server running on `localhost:8001`
- [ ] Frontend server running on `localhost:3001`
- [ ] Test user account created for participant (disposable, not production)
- [ ] Demo data provider confirmed (no real market data)
- [ ] Disclaimer visible on landing page ("Research tool, not investment advice")
- [ ] Observation form printed or ready
- [ ] Recording equipment tested (if recording)
- [ ] Consent form ready for signature

### At the start of the session

- [ ] Greet participant and introduce yourself
- [ ] Explain the pilot purpose (two questions: usability and value)
- [ ] Review consent form and obtain signature (or verbal agreement)
- [ ] Confirm recording permission (if applicable)
- [ ] Explain: "This uses demo data, not real market data"
- [ ] Explain: "Results are for demonstration only"
- [ ] Explain: "This is a research tool, not investment advice"
- [ ] Ask: "Are you comfortable sharing your research example, or would you prefer a hypothetical one?"
- [ ] If sharing: "Please remove any sensitive information like specific holdings, account values, or proprietary data"
- [ ] Start recording (if applicable)
- [ ] Note start time

### During the session

- [ ] Follow the three-level assistance protocol (observe → neutral prompt → explicit help)
- [ ] Record every intervention in the observation form
- [ ] Record task completion checkboxes
- [ ] Record time breakdown for each task
- [ ] Record participant statements (with consent)
- [ ] Note gaps without building features
- [ ] Do not intervene unless necessary

### At the end of the first session

- [ ] Ask commercial exploration questions (Section 8)
- [ ] Ask: "Is there anything else you'd like to share?"
- [ ] Schedule the return session (1-4 weeks later)
- [ ] Remind participant: "Your account and data will be preserved until the return session"
- [ ] Stop recording (if applicable)
- [ ] Note end time
- [ ] Thank participant

### Between sessions

- [ ] Do NOT delete participant account or data
- [ ] Anonymise observation notes
- [ ] Synthesize observations (separate from interpretations)
- [ ] Prepare for return session

### At the start of the return session

- [ ] Greet participant and welcome back
- [ ] Confirm they still consent to participate
- [ ] Start recording (if applicable)
- [ ] Note start time

### During the return session

- [ ] Follow the return-session task instructions (Section 6)
- [ ] Ask: "Did you use Alethra since our last session?" (if yes, record as spontaneous reuse)
- [ ] Observe retrieval of original reasoning
- [ ] Observe interpretation of changes
- [ ] Observe decision-making process
- [ ] Record observations in the form

### At the end of the return session

- [ ] Ask: "Would you be interested in trying another research example?"
- [ ] Ask: "Under what circumstances would you use this tool again?"
- [ ] Ask: "What would make you reject this tool?"
- [ ] Stop recording (if applicable)
- [ ] Note end time
- [ ] Thank participant
- [ ] Inform participant: "Your data will be deleted on [date]. Would you like a copy before then?"

### After the session

- [ ] Anonymise all observation notes
- [ ] Delete recordings according to owner-approved retention policy
- [ ] Compare observations to decision rules (Section 9)
- [ ] Report to owner with recommendations
- [ ] Do not invent results or calculate unsupported time savings

---

## 17. Blank Observation Form

**Copy this form for each participant and session.**

---

### Session metadata

- **Participant ID:** [anonymised, e.g., P01]
- **Session date:** [YYYY-MM-DD]
- **Session type:** [first / return]
- **Moderator:** [name]
- **Start time:** [HH:MM]
- **End time:** [HH:MM]
- **Duration:** [minutes]

### Demo usability (Question 1)

**Task completion:**

- [ ] Hypothesis recorded
- [ ] Falsification criteria recorded
- [ ] Original idea recorded
- [ ] Backtest executed (first session only)
- [ ] Method review inspected
- [ ] Timeline inspected
- [ ] Decision explained

**Time breakdown:**

| Task | Elapsed time | Context |
|------|--------------|---------|
| Setup | [min] | [e.g., "familiar with forms"] |
| Hypothesis entry | [min] | [e.g., "paused to think"] |
| Backtest execution | [min] | [e.g., "expected faster"] |
| Method review | [min] | [e.g., "read carefully"] |
| Decision explanation | [min] | [e.g., "clear reasoning"] |

**Moderator interventions:**

| Timestamp | Level | Prompt/Help provided | Participant response |
|-----------|-------|---------------------|---------------------|
| [mm:ss] | [1/2/3] | [exact words or "none"] | [what they did next] |
| [mm:ss] | [1/2/3] | [exact words or "none"] | [what they did next] |
| [mm:ss] | [1/2/3] | [exact words or "none"] | [what they did next] |

**Repeated inputs:**

- [ ] Re-entered hypothesis
- [ ] Re-ran backtest
- [ ] Re-read the same section
- [ ] Other: [describe]

**Errors and confusion:**

- **Timestamp:** [mm:ss]
- **What happened:** [describe]
- **Participant statement:** [quote with consent]
- **Resolution:** [self-corrected / moderator assistance / abandoned]

### Research-workflow value (Question 2)

**Participant's current workflow (from interview):**

- Tools used: [describe]
- Time spent: [per week/month]
- Pain points: [describe]
- Recent strategy change: [describe]

**Does Alethra address their pain?**

- [ ] Yes, participant described a concrete fit
- [ ] Partially, some features address some pain
- [ ] No, participant did not see a fit
- [ ] Unclear, participant could not articulate pain

**Evidence:**

- Participant statements: [quotes]
- Observed behaviour: [describe]
- Moderator interpretation: [describe]

### Return session (if applicable)

**Spontaneous reuse:**

- [ ] Participant used Alethra between sessions without prompting
- [ ] Participant did not use Alethra between sessions

**If yes, what did they do?**

- [describe]

**Retrieval of original reasoning:**

- [ ] Retrieved easily
- [ ] Retrieved with difficulty
- [ ] Could not retrieve

**Decision-making:**

- [ ] Retain strategy
- [ ] Revise strategy
- [ ] Stop strategy
- [ ] Wait for more evidence
- [ ] Unclear

### Commercial exploration

**Current alternatives:**

- [describe]

**Reasons to adopt:**

- [describe]

**Reasons to reject:**

- [describe]

**Circumstances for payment:**

- [describe]

### Interest in another task

- [ ] Participant asked to try another research example
- [ ] Participant declined
- [ ] Participant suggested a different use case

### Observations (facts)

- [list observations]
- [list observations]
- [list observations]

### Interpretations (moderator judgment)

- [list interpretations]
- [list interpretations]
- [list interpretations]

### Recommendations

- [describe]

---

**End of observation form.**

---

## 18. Final Statement

**Pilot materials prepared for owner review.**

**No users contacted, content published, application deployed, pricing activated or product features added.**

**Corrections made:**
1. ✓ Separated demo usability testing from validation of actual research-workflow value
2. ✓ Included optional redacted participant-owned research example (no broker credentials or sensitive data)
3. ✓ Reconciled return sessions with account/data deletion (do not delete between sessions)
4. ✓ Left retention period, recording permission, and access policy explicitly awaiting owner confirmation
5. ✓ Separated scheduled/reminded return visits from spontaneous reuse
6. ✓ Replaced "observe without intervening" with consistent three-level assistance protocol
7. ✓ Marked ≥3 months as recruitment heuristic, not proven threshold
8. ✓ Added invitation draft, consent draft, moderator checklist, and blank observation form

**Next steps (owner decision required):**
1. Review this pack
2. Decide on recruitment channels and participant compensation
3. Decide on moderator identity and session format
4. Approve pilot timeline and success criteria
5. **Approve data retention period, recording permission, and access policy**
6. Review and approve invitation draft (Section 14)
7. Review and approve consent draft (Section 15)
8. Begin recruitment (after all approvals)

**This is not a commercial launch. This is a small pilot to gather directional evidence.**

---

**End of pack.**
