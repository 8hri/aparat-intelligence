# قالب‌های خروجی (Output Formats)

هر ویدیو یک فضای کار اختصاصی دارد:

```text
<output_dir>/<video_id>/
    metadata.json        فراداده نرمالایزشده
    transcript.json      سگمنت‌ها با start/end (ثانیه)
    transcript.md        متن با مهر زمانی [HH:MM:SS]
    visual_analysis.json یافته‌های هر فریم با برچسب منبع (source)
    summary.md           خلاصه اجرایی (تولیدشده توسط مدل در مکالمه)
    study_notes.md       جزوه مطالعاتی (تولیدشده توسط مدل در مکالمه)
    quiz.md              نسخه آزمون (تولیدشده توسط مدل در مکالمه)
    flashcards.md        نسخه فلش‌کارت‌ها (تولیدشده توسط مدل در مکالمه)
    report.md            گزارش کامل ترکیبی
    report.txt           نسخه متنی ساده (فقط وقتی --text-only)
```

## مهر زمانی

ساختار یکسان در همه فایل‌ها: `[HH:MM:SS]`. مثال: `[00:02:30]`.

## منبع شواهد (Provenance)

هر یافته در `visual_analysis.json` باید فیلد `source` داشته باشد:

| source | معنی | نباید به‌عنوان ... ارائه شود |
|--------|-------|-------------------------------|
| `ocr` | متن استخراج‌شده از روی فریم با OCR محلی | پیاده‌سازی گفتار یا درک تصویری |
| `vision` | مشاهدات تصویری واقعی توسط مدل با قابلیت تصویر | متن OCR |
| `metadata` | توضیحات/فراداده رسمی ویدیو | پیاده‌سازی گفتار یا تصویر |

گزارش (`report.md`) یافته‌ها را بر اساس `source` در بخش‌های جداگانه و
برچسب‌گذاری‌شده گروه‌بندی می‌کند و خلاصه‌ای از منابع شواهد را ارائه می‌دهد.

## coverage.json

پس از استخراج فریم‌ها، اعتبارسنجی پوشش در `<cache_dir>/<video_id>/frames/coverage.json`:

```json
{
  "covered": true,
  "video_duration": 1476.0,
  "frame_count": 20,
  "first": 0.0,
  "last": 1475.9,
  "tail_seconds": 0.1,
  "gaps": [],
  "max_gap_seconds": 0.0,
  "note": "OK: full duration covered"
}
```

`covered` فقط زمانی `true` است که مجموعه فریم‌ها کل مدت ویدیو (شامل
پایان آن) را با تلورانس یک بازه نمونه‌بندی پوشش دهد.

## metadata.json

```json
{
  "video_id": "VIDEO_ID",
  "title": "عنوان ویدیو",
  "description": "توضیحات",
  "uploader": "کانال",
  "duration_seconds": 120,
  "duration": "00:02:00",
  "thumbnail": "https://...",
  "view_count": 1234,
  "like_count": 56,
  "upload_date": "2026-01-01",
  "webpage_url": "https://www.aparat.com/v/VIDEO_ID",
  "extractor": "yt-dlp"
}
```

## transcript.json

```json
{
  "language": "fa",
  "overlap_deduplicated": true,
  "text": "متن کامل پیاده‌سازی‌شده",
  "segments": [
    {"start": 0.0, "end": 2.5, "text": "اولین جمله."}
  ]
}
```

## report.md

هیچ بخش هوشمند حاوی مدل نیست. سکوی گزارش با ساختار کامل رعلم در `report.md`،
بی‌ابطه، و مدل فرآیندهای متنatcher را در مکالمه تولید می‌کند. الفصل‌بندی و
متن پیاده‌سازی شده از `transcript.json` پر می‌شوند؛ خلاصه، جزوه، آزمون و
فلش‌کارت توسط مدل در مکالمه نوشته و در فایل‌های جداگانه ذخیره می‌شوند.

## مقایسه چند ویدیو

`compare` در `compare/` از فایل‌های `comparison.json` ذخیره می‌شود. ادعای
محتوا با `video_id` منبع همراه است.

## زبان

فارسی زبان پیش‌فرض همه گزارش‌هاست. زبان پیاده‌سازی متن صوت خودکار تشخیص
داده می‌شود یا با `--language` مشخص می‌شود.
