# تقرير الإصلاح الجذري والتدقيق النهائي لنظام Mkmoon

**المشروع:** Mkmoon — Binance Spot، Paper-first، LONG-only

**النطاق:** إصلاحات تشغيلية ومنطقية وقابلة للتدقيق، مع منع التداول الحي منعًا نهائيًا في هذه المرحلة.

**الحالة الموثقة:** commit `8a88e40` على `main`، وخدمة Render الحالية `https://mkmoon.onrender.com`.

## الخلاصة التنفيذية

اكتمل إصلاح المسار الأساسي من جلب الشموع المغلقة إلى القرار والتخزين وPaper execution والتدقيق الدوري. أصبحت الخدمة الإنتاجية تعمل كـWeb Service واحد يحتوي Embedded Paper Worker، مع قفل Redis موزع، تمديد تلقائي للقفل أثناء الدورة، مهلة قصوى للدورة، ونتيجة منظمة تعكس حالة الدورة الفعلية. لا توجد أي مكالمات خاصة إلى Binance ولا أي أوامر حية؛ تأكد الإنتاج من `trading_mode=paper` و`live_trading_enabled=false`، ومن جاهزية PostgreSQL وRedis.[1] [2]

آخر smoke test موثق أعاد أن الخدمة online وPostgreSQL وRedis متصلان، وأن آخر دورة مكتملة عالجت 3/3 رموز، وأنشأت 3 قرارات، وسجلت 0 أخطاء و0 أوامر، مع `WATCH=3` و`ENTER=0`. هذا ليس فشلًا في التنفيذ؛ بل نتيجة متوقعة لأن بوابة الدخول ما زالت مغلقة عمدًا إلى أن تتوافر معايرة EV موثقة.[3] [4]

## المشاكل الجذرية والإصلاحات

| المشكلة الجذرية | الأثر قبل الإصلاح | الإصلاح المنفذ | حالة التحقق |
|---|---|---|---|
| Paper execution كان stubًا ولا ينشئ order أو fill أو position | قرار ENTER لا ينتج دورة حياة قابلة للتدقيق | أضيف simulator للدخول والخروج، وعمليات PostgreSQL ذرية للحجز والأمر والـfill والمركز وأحداث lifecycle | اختبارات المحاكاة ناجحة، وschema الإنتاج مؤكد؛ لا توجد صفقة إنتاجية لأن ENTER=0 |
| عدم وجود idempotency ومنع تكرار المركز | احتمال تكرار أمر لنفس القرار أو مركزين للرمز نفسه | unique decision keys وunique active position per symbol في migration 0006، مع فحص active position داخل المعاملة | مطبق في Supabase ومؤكد read-only |
| EV estimator غير موصول بالقرار | حقول EV ثابتة أو غير صادقة | رُبط `estimate_ev` بمحرك القرار، وتظهر العينة والحالة والمصدر في lineage | آخر قرارات الإنتاج تسجل الحالة، والبوابة ما زالت آمنة |
| `disabled_until_calibrated` كان يمنع الدخول بصمت | لا يعرف المشغل لماذا لا تنشأ الصفقات | أُبقي المنع عمدًا، مع حالة وسبب واضحين؛ لا يُفتح إلا بعد عينة معايرة موثقة | آخر دورة `WATCH=3` و`ENTER=0` |
| تقريب السعر كان يستخدم ROUND_DOWN للاتجاهين | احتمال stop أو target غير صالحين هندسيًا | الاتجاه `down` يقرب لأسفل، والاتجاه `up` يقرب لأعلى، مع تحقق `stop < entry < target` | اختبارات regression للمخاطر ناجحة |
| دورة Worker كانت بطيئة ومتسلسلة | دورة 3 رموز كانت تستغرق أكثر من دقيقتين | Semaphore واحد محدود على مستوى الدورة، مهلات HTTP، و`CYCLE_TIMEOUT_SECONDS` | آخر دورة مكتملة نحو 3.5 ثوانٍ |
| لا يوجد distributed lock فعلي | تشغيل نسختين قد يكرر التحليل أو Paper trade | قفل Redis حول الدورة كاملة، heartbeat لتمديد TTL، وفشل مغلق في production عند غياب Redis | summary يسجل `lock=redis:paper-cycle`، وready يتطلب Redis |
| حالة Worker كانت قد تقول FINISHED بعد فشل داخلي | Dashboard مضلل أثناء الأعطال | `CycleResult` منظم، status/error/duration/source محفوظة من النتيجة الفعلية | لا توجد أخطاء في آخر دورة |
| دورات RUNNING قد تبقى عالقة بعد restart | Cycle Summary غير قابل للتشخيص | `recover_stale_cycles` يغلق الدورات القديمة كـFAILED مع سبب استرداد وتوقيت الاسترداد | SQL مصحح بعد ظهور AmbiguousParameterError في أول deploy، ثم اختفى الخطأ |
| مصدر Worker ومصدر أسعار المتصفح غير مميزين | احتمال تفسير Binance.US كأنه Binance.com | كل دورة تسجل `sources_seen` و`binance_data_source`، وDashboard يوضح أن WebSocket العرض ليس مصدر تنفيذ | آخر دورة سجلت المضيفين الناجحين؛ fallback Binance.US سيظهر صراحة عند استعماله |
| عدادات Dashboard كانت محدودة بعينة العرض | الرقم الظاهر لا يساوي العدد الفعلي | endpoint يستخدم count حقيقي، والواجهة تميز بين الإجمالي والمعروض عند البحث | summary أعاد 102 قرارًا في آخر تحقق |
| تعريف Render كان يحتوي Web Worker إضافيًا | احتمال وجود عاملين متوازيين خارج lock | `render.yaml` أصبح Web Service واحدًا فقط؛ العامل يعمل داخل lifespan | Render أكد Web Service واحدًا وdeploy Live |
| حماية API غير موجودة | logs وorders وdecisions يمكن طلبها دون token | أضيفت بوابة اختيارية `DASHBOARD_ACCESS_TOKEN` ويدعمها Dashboard عبر Bearer token | الكود جاهز، لكن السر غير مضبوط في Render حتى لا يُغلق Dashboard العام دون تسليم آمن للمالك |

## Paper execution بعد الإصلاح

تبدأ صفقة Paper فقط إذا اجتاز القرار كل البوابات وأصبح `ENTER`، ثم يُحاكى fill دخول بسعر adverse slippage ورسوم. يرفض simulator الدخول إذا صار fill خارج هندسة stop/entry/target أو إذا رفعت الرسوم والانزلاقة المخاطر الفعلية فوق `risk_cash`. بعد ذلك تنفذ PostgreSQL معاملة واحدة تنشئ reservation مستهلكًا، وPaper order، وentry fill، وactive position، وحدثي `FIRST_FILL` و`ACTIVATE`، ثم تحدث cash وreserved cash وequity.

لا يُفحص خروج المركز من شمعة سبقت فتحه. في كل دورة لاحقة، تُستخدم شمعة 5m مغلقة بعد `opened_at_ms` فقط. إذا أصابت الشمعة stop وtarget معًا، يطبق النظام قاعدة محافظة: stop أولًا بسبب عدم معرفة ترتيب الحركة داخل الشمعة. عند الخروج، تُنشأ SELL fill، يُغلق الأمر والمركز، يُحرر reservation، ويُحسب صافي PnL بعد رسوم الدخول والخروج، مع lifecycle events `EXIT_REQUEST` و`CLOSED`.

تم إنشاء migration `0006_paper_execution.sql` للحساب والمركز والقيود، و`0007_performance_indexes.sql` لمسارات الاستعلام المرتبطة بالمفاتيح الأجنبية. فحص Supabase أكد وجود أعمدة الحساب والـorders والـfills والمراكز والحجوزات المطلوبة.[5]

## القرار وEV: لماذا لا تظهر صفقات بعد؟

هذا السلوك مقصود وآمن، وليس عطلًا. سياسة `edge.disabled_until_calibrated` تمنع أي دخول قبل امتلاك عينة نتائج Paper كافية ومعايرة قابلة للإثبات. لذلك لا يجوز تغييرها إلى enabled لمجرد رؤية أوامر في Dashboard. المسار العلمي التالي، خارج هذا الإصلاح، هو جمع نتائج Paper، التحقق من أن كل fill مرتبط بقرار ومصدر ونسخة config، ثم حساب العينة وEV على بيانات نتائج حقيقية. أي فتح للبوابة يجب أن يكون تغيير سياسة مستقلًا ومراجعًا، وليس تعديلًا صامتًا أثناء التشغيل.

## مصادر البيانات والـ25 أصلًا

واجهة Dashboard تعرض قائمة 25 زوجًا وتستقبل أسعار العرض من Binance WebSocket العام في المتصفح، مع REST fallback. هذه الأسعار للعرض وليست مصدرًا لأوامر التنفيذ. Worker الإنتاجي يعمل حاليًا على 3 رموز فقط لأن Environment اليدوي في Render ما زال مضبوطًا على `BTCUSDT,ETHUSDT,SOLUSDT`. لم أوسّع Worker إلى 25 تلقائيًا؛ ذلك غير آمن قبل فحص توفر كل الأزواج على المصدر الذي يجيب Render، خصوصًا أن `https://api.binance.us` fallback رسمي مستقل وليس نسخة مطابقة من Binance.com.

المصدر الفعلي يُحفظ في lineage وCycle Summary. وجود أكثر من host في `sources_seen` يعني مضيفين استُخدما أو أجيبت منهما طلبات الدورة، ولا يعني تطابق دفاتر الأسعار أو السيولة بين Binance.com وBinance.US. إذا كان المطلوب تطابق Binance.com حرفيًا، فالحل التشغيلي ليس إخفاء fallback، بل توفير مسار egress موثوق إلى Binance.com ثم توثيقه واختباره منفصلًا.

## الاختبارات والتحقق الإنتاجي

نفذت النواة `compileall` بنجاح، ونجحت مجموعة الاختبارات الحالية بنتيجة **24 passed, 1 skipped**. تشمل الاختبارات الحالية fallback العام، حماية private client من التداول في Paper، محاكاة entry/exit المحافظة، وتقريب المخاطر وهندسة الأسعار. كما طُبقتا migration 0006 و0007 على Supabase، وتأكدت عقود الجداول بقراءة `information_schema` دون تعديل بيانات تشغيلية.

Render نفذ deploy `6401d65` بنجاح وأعلن الخدمة Live. وبعده أعاد `/health` حالة production/Paper، وأعاد `/ready` حالة ready، وأعادت آخر دورة `COMPLETED` مع 3/3 رموز و0 أخطاء. ظهر `AmbiguousParameterError` في محاولة أولى لاسترداد الدورات العالقة؛ عُزل سببه في معاملات timestamps غير ذات النوع الصريح، وأُصلح في commit `008e52c` بإضافة casts إلى `bigint`، ثم اختفى في smoke test التالي.[1] [2] [3]

## ما بقي مقصودًا أو يحتاج قرارًا مستقلًا

| البند المتبقي | السبب | الإجراء الآمن التالي |
|---|---|---|
| لا توجد Paper orders إنتاجية حتى الآن | لا توجد قرارات ENTER بسبب بوابة EV والمعايير الحالية | لا تفتح البوابة؛ اجمع عينة معايرة Paper أولًا |
| Worker يحلل 3 رموز لا 25 | Environment الفعلي في Render لم يُحدّث، وBinance.US قد لا يدعم كل قائمة 25 | افحص intersection مع المصدر الفعلي ثم غيّر Environment يدويًا إذا ثبتت التغطية |
| `DASHBOARD_ACCESS_TOKEN` غير مضبوط | تفعيله دون تسليم آمن للtoken قد يقفل المالك خارج Dashboard | أنشئ secret جديدًا من Render، ثم افتح Dashboard عبر `?dashboard_token=...` مرة واحدة؛ لا تضعه في GitHub أو رسالة عامة |
| Free Render قد يوقف الخدمة عند عدم النشاط | قيد استضافة خارجي، وليس خطأ في Worker | لا تُعتبر الخدمة ضمانًا لـ24/7؛ استخدم خطة تشغيل دائمة فقط إذا كان ذلك مطلوبًا وبعد قرار تكلفة مستقل |
| fallback Binance.US ليس Binance.com | اختلاف مصدر وبيانات وسيولة | أبقه معلنًا، أو وفّر egress إلى Binance.com قبل أي مقارنة تشغيلية |

## الملفات والـcommits المهمة

| العنصر | الرابط |
|---|---|
| كود المشروع | [mkkh01/Mkmoon على GitHub][6] |
| آخر commit والتحقق | [commit 8a88e40][7] |
| تقرير baseline قبل الإصلاح | [`docs/root_audit.md`][8] |
| سجل التحقق الإنتاجي | [`docs/deployment_verification_2026-08-23.md`][9] |
| Migration Paper runtime | [`migrations/0006_paper_execution.sql`][10] |
| Migration فهارس الأداء | [`migrations/0007_performance_indexes.sql`][11] |

## الحكم النهائي

النظام الآن صالح للتشغيل **Paper-only** والتحليل الدوري القابل للتدقيق، وليس صالحًا ولا مهيأً للتداول الحي، وهذا مقصود. تم إصلاح عيوب التنفيذ الورقي، provenance، التزامن، الاسترداد، المخاطر، الأداء، وCycle Summary من جذورها ضمن الكود والمخطط. أما فتح الدخول أو تفعيل 25 رمزًا أو الانتقال إلى Binance private/live فهي تغييرات تشغيلية جديدة تتطلب تحققًا منفصلًا وموافقة صريحة، ولا ينبغي اعتبارها جزءًا من هذه المهمة.

## المراجع

[1]: https://mkmoon.onrender.com/health "Mkmoon production health"
[2]: https://mkmoon.onrender.com/ready "Mkmoon production readiness"
[3]: https://mkmoon.onrender.com/api/dashboard/summary "Mkmoon production dashboard summary"
[4]: https://mkmoon.onrender.com/api/cycles?limit=1 "Mkmoon latest cycle summary"
[5]: https://github.com/mkkh01/Mkmoon/tree/main/migrations "Mkmoon database migrations"
[6]: https://github.com/mkkh01/Mkmoon "Mkmoon GitHub repository"
[7]: https://github.com/mkkh01/Mkmoon/commit/8a88e40 "Final production smoke-test documentation commit"
[8]: https://github.com/mkkh01/Mkmoon/blob/main/docs/root_audit.md "Root-cause baseline audit"
[9]: https://github.com/mkkh01/Mkmoon/blob/main/docs/deployment_verification_2026-08-23.md "Deployment verification record"
[10]: https://github.com/mkkh01/Mkmoon/blob/main/migrations/0006_paper_execution.sql "Paper execution schema migration"
[11]: https://github.com/mkkh01/Mkmoon/blob/main/migrations/0007_performance_indexes.sql "Paper and audit performance indexes"
