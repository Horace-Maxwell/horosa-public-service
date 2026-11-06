---
name: horosa
description: Use the Horosa 星阙 tools whenever the user asks to cast, compute or explain a chart or divination — Western natal or predictive charts, Indian charts, BaZi, Zi Wei Dou Shu, Qi Men Dun Jia, Da Liu Ren, Liu Yao, Xiao Liu Ren, tarot, Chinese lunar calendar, almanac or solar terms. Every calculation is a separate read-only tool; confirm result-changing settings first, explain only from the returned sections, and never calculate by hand.
---

# Horosa 星阙 — how to work with the calculation tools

Horosa computes charts with its own engines and returns the engine's text sections plus a technique card.
Your job is to collect the inputs, confirm the settings that change the result, call exactly one tool per
calculation, and explain the returned sections. These outputs are cultural and entertainment references.

## 1. Pick the tool

### Western astrology
- `horosa_astro_chart` — Western natal chart / 西洋本命盘 — birthplace or event place (city level) required
- `horosa_astro_relative` — Relationship charts / 合盘 — birthplace or event place (city level) required
- `horosa_chart_horary` — Horary chart / 卜卦占星 — no place input (cast for here and now)
- `horosa_chart_election` — Electional chart / 择时单盘 — birthplace or event place (city level) required
- `horosa_predict_solarreturn` — Solar return / 太阳返照 — birthplace or event place (city level) required
- `horosa_predict_lunarreturn` — Lunar return / 月亮返照 — birthplace or event place (city level) required
- `horosa_predict_profection` — Annual profections / 小限推运 — birthplace or event place (city level) required
- `horosa_predict_firdaria` — Firdaria periods / 法达星限 — birthplace or event place (city level) required
- `horosa_predict_progressions` — Secondary progressions / 次限推运 — birthplace or event place (city level) required
- `horosa_predict_solararc` — Solar arc directions / 太阳弧 — birthplace or event place (city level) required
- `horosa_predict_ephemeris` — Planetary ephemeris / 星历 — birthplace or event place (city level) required

### Indian and seven-luminaries astrology
- `horosa_astro_india_chart` — Indian astrology chart / 印度占星 — birthplace or event place (city level) required
- `horosa_astro_guolao_chart` — Seven luminaries chart / 七政四余 — birthplace or event place (city level) required

### Chinese techniques
- `horosa_cn_bazi_birth` — Bazi birth chart / 八字 — birthplace optional; omitted = clock time
- `horosa_cn_ziwei_birth` — Ziwei Doushu chart / 紫微斗数 — birthplace optional; omitted = clock time
- `horosa_cn_qimen` — Qimen Dunjia chart / 奇门遁甲 — no place input (cast for here and now)
- `horosa_cn_liureng_gods` — Da Liu Ren chart / 大六壬 — birthplace optional; omitted = clock time
- `horosa_cn_sixyao` — Six-line divination / 六爻 — no place input (cast for here and now)
- `horosa_cn_xiaoliuren` — Xiao Liu Ren / 小六壬 — no place input (cast for here and now)

### Calendar and tarot
- `horosa_cn_nongli_time` — Chinese lunar calendar / 农历换算 — no place input (cast for here and now)
- `horosa_cn_huangli` — Chinese almanac / 黄历 — no place
- `horosa_cn_jieqi_year` — Yearly solar terms / 二十四节气 — birthplace or event place (city level) required
- `horosa_cn_tarot` — Tarot reading / 塔罗 — no place

`horosa_agent_guidance` explains the settings of one tool (pass its `tool_name`) and runs nothing. Use it when
you are unsure which settings matter.

## 2. Collect inputs honestly

- Dates are `YYYY-MM-DD`, times `HH:mm:ss` (24-hour), timezones a UTC offset such as `+08:00` or an IANA name.
- Birth charts need the birthplace at **city level**; a city name resolved to two-decimal coordinates is enough.
  Never ask for a street address or GPS reading, and never substitute the user's current location for a birthplace.
- Charts cast for the present moment (Qi Men, Liu Yao, Xiao Liu Ren, lunar date, horary) take **no place input**.
  When the client provides a coarse location hint the server uses it; otherwise the server computes with clock time
  and says so in `warnings`. Do not invent coordinates to work around this.
- Do not pass names, questions about third parties, or anything the tool does not list in its schema.

## 3. Confirm settings before calculating

A tool answers `agent_guidance.required` with `agent_recovery.prompt_to_user` when a result-changing setting is
missing. Ask the user, then retry with `agent_confirmed_settings: true` — or with `defaults_accepted: true` only
when the user explicitly accepts the Horosa defaults. Never set either flag on your own initiative.

## 4. Explain only what came back

- Quote and interpret the returned `sections`; do not add positions, pillars or cards that are not in them.
- Repeat the `technique_card` settings (house system, zodiac, time convention, school) so the user knows what was used.
- Pass `warnings` on to the user in plain words (for example clock-time computation, omitted sections, truncation).
- Never calculate anything yourself — no mental ephemeris, no manual stem-branch arithmetic.

## 5. Boundaries

- No lifespan, illness, death, medical, legal or financial conclusions, even if asked.
- If a request is outside these tools (booking, purchases, other services), say so and call nothing.
- Treat every reading as a traditional-culture and entertainment reference.
