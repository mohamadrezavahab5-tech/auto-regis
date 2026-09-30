# 05 - شیت Online-Instore و داده CRM (دیده‌شده در Chrome)

## شیت دوم: Online-Instore (Google Sheet مشترک با تیم Online Insta)
تب‌ها: **Row Data** (محافظت‌شده، ورودی خودکار) | **Pending** | **Online Approved** | **قدیمی**
- Row Data (ستون‌ها): Case ID، Created At، Brand Name، EN Name، Category، Commission، Settlement، City، Address، Main Status (CANCELLED/PENDING/...)،
  Data Entry Stage (OWNER_INFO، BASIC_INFO، SETTLEMENT_INFO، CONTRACT_SIGNING...)، Channel Status (BOTH...)، Duplicate Check، Duplicate Rating، Cleaning Done?،
  Similar Case IDs، Similar Status، Sub Reason، owner Family، Owner Mobile، referral code، ownership_type (INDIVIDUAL/LEGAL)، updated_at، Last Update.
  نکته: این تب هر چند دقیقه ردیف تازه می‌گیرد (Case ID با زمان همان روز) - یعنی یک خوراک خودکار از NBO وجود دارد؛ منبع دقیقش را باید پرسید.
- Pending: سه گروه ستون کنار هم:
  Portal (Date Update Portal Status، Main Status، Update Portal Status) | Instore (Date Instore Check، بررسی قرارداد، دلیل نیاز به ادیت، دلیل لغو قرارداد) |
  Online (Date Online Check، بررسی قرارداد، دلیل نیاز به ادیت، دلیل لغو قرارداد) | Notes | Case ID، Brand Name، Category، City، Main Status، Similar Case IDs، Similar Status، updated.
  یعنی تیم Instore و تیم Online (ما) هر کدام نتیجه بررسی و دلیل خودشان را می‌نویسند و Portal Status جدا به‌روز می‌شود.
- تب مقصد Apps Script «pending» در Main-Data با همین ساختار است؛ SMR در ستون M، Status در I، دلایل J و K، تاریخ H.

## داده CRM (Dynamics 365، crm.snapppay.ir)
مسیر: Sales > Merchant Registrations > نمای «Active Merchant Registrations» (250 ردیف در هر صفحه از 5000+).
ستون‌های نما: کد درخواست (MRG-...)، تاریخ ایجاد، نام تجاری، وضعیت، نوع قرارداد (حقیقی/حقوقی)، نوع فروشگاه (شبکه اجتماعی/آنلاین/...)، نام و نام‌خانوادگی مالک، وضعیت درخواست پذیرنده (مثل «در انتظار اطلاعات طرف قرارداد»، «در دست بررسی تیم فروش»، «پیشنویس قرارداد»).
Export: دکمه‌های نوار بالا (Excel Templates / CSV / Dashboard). فیلتر: قرارداد حقوقی=No، Store Type=Online، Status=All.
