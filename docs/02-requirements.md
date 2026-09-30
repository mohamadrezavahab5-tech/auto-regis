# 02 - Requirements (از توضیح شفاهی جلسه + متن‌های رسمی‌شده)

منبع: توضیح شفاهی صاحب پروژه (متن GPT از ویس جلسه) + سندهای ساختاریافته‌ای که او چسباند.
تصحیح: متن GPT از ویس، NBO را «EMIO / Embio / EMBU» نوشته بود؛ سیستم مبدأ همه‌جا **NBO** است (nbo.snapppay.ir).
علامت [؟] یعنی «قطعی نیست، باید از کد/صفحه NBO عیناً برداشته شود». چیزی در این سند حدسی نیست.

## هدف
هر درخواست Pending (ثبت‌نام پذیرنده آنلاین) باید یکی از این نتیجه‌ها را بگیرد: Approve / Cancel(Reject) / Edit / Manual.
اصل حاکم: **اگر سیستم مطمئن نیست، Action نزند؛ Manual.** Manual «خطا» نیست، مکانیزم ایمنی است.

## سه لایه
Data Layer (NBO / CRM / Main / Daily Pending) -> Decision Layer (Duplicate، Enamad، Category، Rules) -> Action Layer (Action Test / Reject Engine).

## ترتیب اجرای روزانه (باید در خود سیستم ثبت شود، نه در ذهن اپراتور)
1. Export مرجع NBO  2. Export مرجع CRM (Search > Merchant Registration؛ قرارداد حقوقی=No، Store Type=Online، Status=All)  3. Legal NBO (دستی، حجم کم)
4. Update NBO -> Main  5. Update CRM -> Main (Google Apps Script؛ با «Continue» بعد از محدودیت زمان اجرا)
6. ساخت NBO Approved و CRM Approved (Statusهای Approved / Activating / Complete / Pending Activation و معادل CRM)
7. Export Pendingهای روز از NBO: Online + Individual + Status در {Pending, Commercial in Progress}. (Online Insta و Legal وارد نمی‌شوند)
8. ریختن در Daily Pending -> چک Duplicate روی Website/SMR در NBO Approved + CRM Approved -> Tick
9. فیلتر Tick -> کپی کد درخواست‌ها -> Excel موتور Cancel (Duplicate) -> اجرا؛ قبل از Cancel حتماً Type = Online کنترل شود
10. باقی‌مانده (غیر Duplicate) = Pending خالص -> Main Check Engine
11. Check Engine: از **جدیدترین** به قدیمی‌ترین، Batch حدود 100 تا 200، هر Instance Excel و Chrome/Profile خودش؛ Login + OTP دستی در شروع
12. خروجی: Approve/Edit/Cancel/Manual + Reason؛ Manual: مرورگر باز می‌شود تا اپراتور SMR را دستی جستجو کند
13. Action Engine فقط تصمیم‌های ثبت‌شده در Excel را اجرا می‌کند.

## Online Insta (Flow جدا ولی وابسته به Main)
شیت مشترک دو تیم؛ هر تیم ستون خودش. فیلتر ستون «بررسی ما» روی Blank و Marker داخلی [؟ نام دقیق Marker از شیت] -> کپی به Main -> Check -> منوی «اجرای کد بررسی» نتیجه را به شیت Online Insta برمی‌گرداند.

## قواعد Check (ترتیب منطقی)
Website باز شود؟ -> Enamad پیدا شود؟ -> نام صاحب اینماد = Account Holder در NBO؟ -> Category اینماد با Category در NBO سازگار؟ (ناشناخته = Manual) -> اینماد منقضی نباشد -> اینماد روی سایت نمایش داده شود [؟] -> Agreement معتبر [؟] -> Gold و Special = Manual (مدرک لازم) -> تعداد محصول متناسب با Category -> اطلاعات تماس/پشتیبانی موجود -> Pass.
- Fixable (مرچنت می‌تواند اصلاح کند) = **EDIT**؛ Invalid صریح = **CANCEL**؛ نامطمئن = **MANUAL**.
- تعداد محصول: حدود 40 [؟] عمومی؛ خدمات و آموزشی کمتر از حدود 10 [؟]. Gold و خدمات را خودکار نگیر [؟ تأیید شود].
- Reason **عیناً** همان متن NBO؛ یک Reason Registry مرکزی (Internal Rule -> Action -> Exact NBO Reason).
- Name matching: Normalize (نیم‌فاصله، ی/ک عربی، ترتیب نام) ولی هرگز حدس با شباهت کم.

## نیازهای نسخه جدید
Queue Manager، Status داخلی هر درخواست (IMPORTED ... COMPLETED/MANUAL/FAILED/RETRY)، قفل جلوگیری از پردازش دوباره، Resume، Live Re-check قبل از Action (Status/Online/Action قبلی)، Audit Log کامل هر درخواست (تاریخ، منبع، Check، Decision، Reason، Action)، Dashboard، Dry-run.
اجرا: ابتدا فقط **PLAN MODE** (بدون اجرا روی Production)، با تب‌های اضافه در شیت خود او؛ هر تب بعد از تأیید صریح او فعال شود.

