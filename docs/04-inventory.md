# 04 - موجودی واقعی اسکریپت‌ها و شیت‌ها (از خواندن خود Apps Script)

منبع: پروژه Apps Script بسته به شیت «Main-Data» (فقط‌خواندنی؛ چیزی تغییر نکرد).
وضعیت خواندن: فایل‌های 2، 5، 6، 8 کامل خوانده شدند؛ فایل 1 (Dashboard) فقط ابتدایش؛ فایل‌های 3 (update-crm)، 4 (گزارش روزانه وضعیت‌ها) و 7 (utils) **هنوز خوانده نشده‌اند**.

## تب‌های شیت Main-Data
CRM | NBO | LEGAL | Category-commission | داشبورد | NBO Approved | CRM Approved | Daily Pending | duplicate | online-instore | action-online-instore | Adress-link

## اسکریپت‌ها (Apps Script)
| فایل | تابع | کار | ورودی | خروجی |
|---|---|---|---|---|
| update-nbo.gs | importNBOFull / continueNBO / runNBO / autoNBO / checkNBOCount | CSV «nbo-data.csv» را خوانده و تب NBO را پاک و با تکه‌های 500 ردیفی دوباره می‌نویسد؛ سقف 3 دقیقه؛ اگر تمام نشد ردیف آخر را ذخیره و Trigger هر 5 دقیقه نصب می‌کند | فایل CSV (Drive) | تب NBO |
| update-crm.gs | (خوانده نشده؛ ساختار مشابه) | همان برای CRM | CSV | تب CRM |
| بررسی تکراری ها.gs | جمع_آوری_تایید_شده_ها | از NBO ردیف‌هایی که Status = COMMERCIAL_APPROVED / COMPLETED / ACTIVATING و از CRM آن‌هایی که «تایید قرارداد / اتمام فعالسازی فنی / درحال فعالسازی فنی» دارند را جمع می‌کند (SMR + سایت، بدون سایت تکراری) | تب‌های NBO و CRM (NBO: A=SMR، D=Status، AG=سایت؛ CRM: A=کد، I=Status، R=سایت) | تب «لیست تایید شده» |
| Daily-Pending.gs | checkDuplicates | برای هر ردیف Daily Pending، سایت را Normalize می‌کند (بدون http/www/مسیر/query/پورت) و در NBO Approved (ستون AG) و CRM Approved (ستون R) می‌جوید | Daily Pending: A=SMR، B=سایت | ستون C (در CRM بود)، D (در NBO بود)، E (SMRهای مشابه) |
| اجرای انلاین اینستور.gs | syncOnlineInstoreToPending | نتیجه (SMR، Status، Reason) از تب online-instore را به تب pending برمی‌گرداند: ستون Status (I)، دلیل Edit (J)، دلیل Reject (K)، تاریخ (H)، رنگ ردیف | تب online-instore ستون A-C | تب pending |
| DashboardHoghooghi.gs | گزارش_نهایی_با_جمع | داشبورد آماری از CRM حقیقی/حقوقی، NBO Online، NBO Offline-Online، LEGAL | تب‌های CRM/NBO/LEGAL | تب داشبورد |
| گزارش روزانه وضعیت ها.gs | (خوانده نشده) | گزارش روزانه | ؟ | ؟ |
| utils.gs | readCsvFileSafe، writeChunkSafe، installContinuationTrigger، removeContinuationTrigger، safeAlert | (خوانده نشده) | | |

## یافته‌های مهم (قابل استناد، از کد)
1. **Duplicate فقط با سایت** تشخیص داده می‌شود (نه SMR، نه نام). بنابراین دو پذیرنده با یک دامنه Duplicate‌اند.
2. **گروه Approved در کد ≠ توضیح جلسه:** جلسه «Approved / Activating / Complete / Pending Activation» گفت؛ کد NBO فقط COMMERCIAL_APPROVED, COMPLETED, ACTIVATING دارد (Pending Activation نیست) - باید تأیید شود.
3. **دو فهرست دلیل متفاوت:** در Apps Script (مثلاً «رابط کاربری مجدد و گمراه کننده»، «فرآیند ثبت‌نام یا خرید دشوار و طلایی»، «وجود محصولات شش و طلایی») با فهرست Python («رابط کاربری پیچیده و گمراه‌کننده»، «...دشوار و طولانی»، «...ششش و طلایی») فرق دارند. یکی از دو متن قطعاً غلط است. نیاز به **Reason Registry** از متن رسمی NBO.
4. **تطبیق فازی دلیل:** فاصله Levenshtein تا 40 پذیرفته می‌شود و اگر نشد متن خام نوشته می‌شود؛ بدون گزارش خطا.
5. **نگاشت Status:** APPROVE -> «تایید قرارداد»، EDIT_NEEDED -> «نیاز به ادیت»، REJECT -> «لغو قرارداد»، MANUAL_REVIEW -> «کم‌کاری کم اولویت - هرتال بررسی نشده». (MANUAL_REVIEW را با ستون دلیل Edit پر می‌کند.)
6. شناسه شیت منبع داخل کد سخت‌نوشته است؛ SYNC_TGT_NAME = «pending» تب مقصد است، نه «Online-Insta».
7. محدودیت زمانی Apps Script (3 دقیقه) و Trigger پنج‌دقیقه‌ای فقط برای دور زدن نبود دسترسی مستقیم به دیتا ساخته شده.

## خواسته صاحب پروژه برای نسخه جدید
تا حد ممکن **بدون Apps Script**؛ یک ابزار یکپارچه، ساده و دقیق که کارهای دستی را خودکار کند.

## نگاشت پیشنهادی: هر اسکریپت -> ماژول ابزار جدید
| قدیمی | جدید |
|---|---|
| update-nbo / update-crm (CSV -> شیت، تکه‌تکه با Trigger) | Import: خواندن مستقیم Export (xlsx/csv) به پایگاه محلی، بدون سقف زمانی و بدون Continue |
| جمع آوری تایید شده ها + Approved | جدول محاسبه‌شده «Approved» از Import (قاعده Status در یک فایل تنظیمات) |
| checkDuplicates | ماژول Duplicate: Normalize سایت، خروجی با شناسه رقیب و دلیل |
| Action Test 4 / reject_engine (Cancel تکراری) | Executor با حالت آزمایشی، بررسی Online و Status پیش از Action |
| Check Engine (action-test*.py) | ماژول Decision با قواعد جدا و تست، Reason از Registry |
| syncOnlineInstoreToPending | خروجی مستقیم به تب/فایل؛ Reason دقیق از Registry، بدون تطبیق فازی |
| داشبورد / گزارش روزانه | صفحه خلاصه از همان پایگاه |
