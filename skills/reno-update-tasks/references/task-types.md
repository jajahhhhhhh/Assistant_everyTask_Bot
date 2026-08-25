# Task Type Keywords

Map natural-language work descriptions to the dashboard's `type` field for color-coded tags.

## Categories

| Type | Color tag | Thai keywords | English keywords |
|---|---|---|---|
| `Electrical` | purple (`tag-elec`) | ไฟฟ้า, เดินสายไฟ, ปลั๊ก, สวิตช์, ไฟ, breaker, หลอด LED | electrical, wiring, outlet, switch, breaker, bulb, lighting |
| `Plumbing` | green (`tag-plumb`) | ประปา, ท่อ, ฝักบัว, ชักโครก, อ่าง, ก๊อก | plumbing, pipe, shower, toilet, sink, faucet, drain |
| `Demo` | red (`tag-demo`) | รื้อ, ทุบ, ถอด, demolish, demo, ทำลาย | demo, demolish, tear out, remove, strip |
| `Structure` | green-yellow (`tag-struct`) | โครงสร้าง, ผนัง, เพดาน, พื้น, หลังคา, เสา, คาน, ฐาน | structure, wall, ceiling, floor, roof, beam, foundation |
| `Finishing` | gold (`tag-finish`) | สี, ทาสี, ปู, กระเบื้อง, ไม้ลามิเนต, วอลเปเปอร์, ขัด | finishing, paint, tile, laminate, wallpaper, sand, polish |
| `General` | gray (`tag-gen`) | (everything else) | (everything else) |

## Inference rules

1. Look at the task title and note. Match the first hit from the table above.
2. If multiple categories hit, prefer the more specific one (`Plumbing` over `General`).
3. If nothing matches, default to `General` — don't guess.
4. Some tasks span types (e.g., "ติดตั้งโคมไฟพร้อมเดินสาย") — pick the primary action (here: `Electrical`).

## Priority inference

| Priority | Thai signals | English signals |
|---|---|---|
| `High` | ด่วน, รีบ, วันนี้, ASAP, สำคัญ, ก่อนเพื่อน | urgent, asap, today, blocker, critical |
| `Medium` | (default) | (default) |
| `Low` | ไม่รีบ, เมื่อว่าง, optional, later | low priority, when possible, nice-to-have, later |
