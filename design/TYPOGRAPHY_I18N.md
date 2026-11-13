# Floe — International typography and localization

**Architecture/design requirement, not certification of worldwide localization.**

## Fonts

Use Noto Sans as the default UI typeface, Noto Sans Mono for monospace content and Manrope only for the editorial wordmark and brand display composition. Floe's system installation adds `noto-fonts`, `noto-fonts-cjk-sans` and `noto-fonts-color-emoji` through NixOS `fonts.packages`.

Noto is a **family collection**. Neither Noto Sans alone nor any one static font file supports every writing system. Fontconfig plus Qt text shaping must choose font/script-specific fallbacks; respect locale-specific differences between Chinese (Simplified/Traditional), Japanese and Korean glyph forms.

Use `fc-match 'Noto Sans'`, `fc-match ':lang=ar'`, `fc-match ':lang=hi'`, `fc-match ':lang=ja'`, `fc-match ':lang=ko'`, `fc-match ':lang=zh-cn'`, `fc-match ':lang=zh-tw'` and inspect how actual target apps draw multilingual paragraphs.

## Example test strings

- Latin + diacritics: Čas a priestor · Příliš žluťoučký kůň
- Greek: Ελληνικά · Ρυθμίσεις
- Cyrillic: Русский · Настройки
- Arabic RTL: العربية · الإعدادات
- Hebrew RTL: עברית · הגדרות
- Devanagari: हिन्दी · सेटिंग्स
- Bengali: বাংলা · সেটিংস
- Tamil: தமிழ் · அமைப்புகள்
- Thai: ภาษาไทย · การตั้งค่า
- Simplified Chinese: 简体中文 · 设置
- Traditional Chinese: 繁體中文 · 設定
- Japanese: 日本語 · 設定
- Korean: 한국어 · 설정
- Symbols: 50% ✓ ∞ ∑ ↔ 🎧 📁

Test RTL mirroring, mixed Latin/Arabic text, input methods, punctuation, numeric shortcuts, font fallback, marks extending beyond usual Latin metrics, menu width, labels with 50% pseudo-localization expansion, search and filing paths. Font coverage alone does not confer correct direction, shaping, UI layout, translation or accessibility.

The Noto families use open font licenses; maintain required license notices for redistributed packages/images and verify details of optional brand fonts separately.

References: https://notofonts.github.io/noto-docs/website/use/ · https://wiki.nixos.org/wiki/Fonts
