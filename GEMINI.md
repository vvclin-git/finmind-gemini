# FinMind Market Morning Report Rules

## Objective
Generate a daily market morning report in Traditional Chinese from structured JSON data.

## Data policy
1. Always read structured JSON first.
2. Never invent any number, percentage, yield, price, date, or news detail.
3. Use only the injected JSON as the source of numeric facts.
4. If a field is missing or a source fails, explicitly write `資料暫缺`.
5. Narrative must follow the data. Do not reverse-engineer numbers from narrative.
6. Do not rewrite dates into relative time such as today, yesterday, or overnight.

## Source ownership
- FinMind
  - Taiwan market recap
  - Taiwan institutional investors
  - USD/TWD
  - Gold
  - Crude oil
- FMP
  - U.S. major indices
- Asia indices
  - Keep placeholder rows when no live provider is configured
- Marketaux
  - Three important financial news items
- U.S. Treasury Fiscal Data
  - Treasury yield validation

## Output language
- Traditional Chinese only

## Required structure
1. 美股與台股盤勢
2. 台股三大法人 / 上市 / 上櫃
3. 美股四大指數
4. 匯率、亞股、原油、黃金
5. 美國債市與關鍵觀察
6. 三則重要財經新聞
7. 晨報總結

## Writing rules
- For sections 1 to 6, keep the code-rendered tables unchanged and write only the summary paragraph after each table block.
- Tables must be built only from the injected JSON.
- Prose may only summarize values already shown in that section's table.
- If a field or whole subsection is missing, render the expected table row and write `資料暫缺` in the missing cells.
- Show numeric facts first, then interpretation.
- Mention exact dates for every market section.
- Use concise finance-oriented wording.
- Keep the final summary to 3 to 5 bullet points.
- When discussing bonds, mention 2Y, 10Y, 30Y, and 10Y-2Y slope if available.
- When discussing equities, distinguish between index move, market breadth or sentiment, and cross-market comparison.
- Keep interpretation conservative. If causality is not explicitly supported by the JSON, use softer wording such as `反映`, `顯示`, or `可能與...有關`, and avoid presenting a cause as certain.
- Preserve traceable factual fields in the news section, especially date, source, and title.
- Preserve direct news links when present.

## Failure handling
- If one source fails, continue with available data.
- Clearly mark missing values or sections as `資料暫缺`.
- Do not silently replace one source with another.
- Do not skip expected rows in debug tables when data is missing.
