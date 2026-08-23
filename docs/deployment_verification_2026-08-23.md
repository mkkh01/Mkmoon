# تحقق نشر Mkmoon — 23 أغسطس 2026

## Render

الخدمة: [https://mkmoon.onrender.com](https://mkmoon.onrender.com)

الخدمة مرتبطة بالمستودع [mkkh01/Mkmoon](https://github.com/mkkh01/Mkmoon) على branch `main`، ومعرّف الخدمة `srv-da4s9ljncjis73ev3hjg`.

آخر commit تم دفعه إلى GitHub: `008e52c` بعنوان `Fix stale cycle recovery parameter types`. صفحة الأحداث أظهرت أن deploy هذا commit بدأ تلقائيًا؛ deploy السابق `43cef6a` أصبح Live. أثناء التحقق كان deploy `008e52c` ما زال في حالة started، ولذلك يلزم إعادة قراءة الأحداث قبل إعلان اكتماله.

التحقق من [health](https://mkmoon.onrender.com/health) أعاد:

```json
{"status":"ok","environment":"production","trading_mode":"paper","live_trading_enabled":false}
```

التحقق من [ready](https://mkmoon.onrender.com/ready) أعاد `{"status":"ready"}`.

## Summary production قبل اكتمال deploy الأخير

[Dashboard summary](https://mkmoon.onrender.com/api/dashboard/summary) أعاد أن الخدمة online، وPostgreSQL وRedis connected، و`dashboard_symbols=25`، بينما `worker_symbols=3` لأن متغير Render اليدوي لم يُحدّث بعد. كانت الأوامر `total=0, open=0, closed=0` والقرارات 70. ظهرت في حالة العامل القديمة `AmbiguousParameterError`، وحدد التدقيق أن السبب في SQL الخاص باسترداد الدورات العالقة؛ عولج بإضافة casts إلى `bigint` في commit `008e52c`.

مصدر الدورة القديمة كان `https://data-api.binance.vision` أو مجموعة مضيفات Binance العامة، ولا يجوز اعتباره تطابقًا مع Binance.com. fallback المعتمد في Render هو `https://api.binance.us` عند فشل المضيفات الأساسية؛ وهو مصدر رسمي مستقل.

## Supabase

المشروع النشط هو `dkeszbmjvkpqljckfifa` في `eu-central-1` وحالته `ACTIVE_HEALTHY`.

تم تطبيق migration `0006_paper_execution` بنجاح لإنشاء `paper_accounts` و`paper_positions`، وmigration `0007_performance_indexes` بنجاح لفهرسة علاقات Paper وaudit. جدول `paper_accounts` يحتوي حساب Paper ابتدائيًا واحدًا بقيمة 10000، ولم تكن هناك Paper orders أو fills عند آخر تحقق.

فحص advisors الأمني أظهر رسائل INFO من نوع RLS enabled no policy للجداول الداخلية؛ هذا يعني أن RLS مفعّل بلا سياسات عامة، وهو مناسب لمنع وصول anon، بينما يستخدم التطبيق اتصال PostgreSQL server-side. فحص الأداء أظهر فهارس FK المطلوبة قبل migration 0007، ولا ينبغي اعتبار ملاحظات unused index في قاعدة صغيرة دليل عطل.

## حدود التحقق

لم يُنفّذ أي private Binance API call أو live order. لم تُضبط `DASHBOARD_ACCESS_TOKEN` في Render، لذلك تبقى حماية API اختيارية وغير مفعلة إلى أن يضع المالك secret جديدًا من لوحة Render. خطة Render في المستودع أصبحت Web Service واحدًا مع Embedded Paper Worker؛ أزيل تعريف Background Worker لمنع التشغيل المزدوج.

## التحقق النهائي بعد deploy 008e52c

بعد إعادة إيقاظ الخدمة، أعاد `/api/dashboard/summary` أن `database=connected` و`redis=connected`، وأن آخر دورة `COMPLETED` بلا أخطاء (`last_cycle_errors=[]`) ومدتها نحو 3.5 ثوانٍ. اختفى `AmbiguousParameterError`.

آخر Cycle Summary مكتمل كان `cycle-1787485929161-b67ee4d9eb`: الحالة `COMPLETED`، عولجت 3/3 رموز، حُفظت 12 مجموعة شموع مغلقة (219 شمعة لكل رمز/إطار)، أُنشئت 3 قرارات، 0 أخطاء، 0 أوامر، وRedis/PostgreSQL ناجحان. القرارات الثلاثة `WATCH` بسبب `SETUP_INCOMPLETE`، وهو متوقع مع بوابة السياسة الحالية، ولا توجد أوامر حية.

المصدر المسجل في هذه الدورة هو `https://api.binance.com` و`https://data-api.binance.vision` كمضيفين ناجحين/مستخدمين في الطلبات. عند الحاجة إلى fallback ستظهر `https://api.binance.us` صراحة في `sources_seen`; لا ينبغي تفسير القائمة على أنها تطابق بين Binance.com وBinance.US.

لا يزال `worker_symbols=3` في بيئة Render الفعلية، رغم أن Dashboard يعرض 25 أصلًا. هذا قرار محافظ صحيح إلى أن يُفحص توفر كامل القائمة على المصدر العامل وتُحدّث Environment يدويًا. لم يُفعّل `DASHBOARD_ACCESS_TOKEN`، لذلك حماية API اختيارية وغير مفعلة حاليًا.

## حالة Render النهائية

أظهرت صفحة Events الصحيحة للخدمة أن deploy `008e52c` أصبح `live` في 11:51 بتاريخ 23 أغسطس 2026. الخدمة الحالية هي Web Service واحد، ولا يوجد Background Worker موازٍ في Render حسب المشروع الفعلي، وهو متوافق مع قرار تشغيل Paper Worker داخل webserver.

## فحص schema النهائي

فحص `information_schema.columns` في Supabase أكد وجود عقود Paper المطلوبة: `paper_accounts` مع cash/equity/reserved/realized PnL، و`paper_orders` مع requested/filled/average fill/fees، و`paper_fills`، و`paper_positions` مع entry/stop/target/exit، و`risk_reservations`. جميع قراءات الفحص كانت read-only وبـ`LIMIT` صريح، ولم تُنشأ صفقة أو تُغيّر بيانات تشغيلية.

## Smoke test النهائي بعد 6401d65

أظهر الفحص الخارجي أن `/health` أعاد production/Paper و`live_trading_enabled=false`، وأن `/ready` أعاد `ready`. PostgreSQL وRedis متصلان، والعامل المضمّن enabled/running وآخر دورة `COMPLETED` بلا `last_cycle_error` أو `last_cycle_errors`، ومدتها نحو 3.5 ثوانٍ.

آخر دورة مفحوصة: `cycle-1787486281810-70306a044a`، الحالة `COMPLETED`، 3/3 رموز، 3 قرارات، 0 أخطاء، 0 أوامر، والقرارات `WATCH=3` و`ENTER=0`. المصدران الناجحان الظاهران هما `https://api.binance.com` و`https://data-api.binance.vision`. ما زال نطاق Worker الإنتاجي 3 رموز، مقابل 25 في واجهة Dashboard، عمدًا إلى حين تحقق قائمة Binance US/مصدر التشغيل كاملًا.

## تحقق نطاق 25 أثناء إعادة التصميم

بعد deploy `bd6625c` أصبح كود Dashboard وتشخيص الاستراتيجيات Live، لكن smoke test ظل يعيد `worker_symbols=3`؛ لذلك لم أعتبر المشكلة محلولة من الكود وحده. تم فتح Environment في Render وتحرير متغير `SYMBOLS` غير السري بالقائمة الكاملة ذات 25 زوجًا بعد التحقق من أن Binance.US يعيد `missing=[]`. بدأت Render إعادة deploy بسبب تحديث Environment عند 12:25، وما زال التحقق النهائي ينتظر دورة جديدة بعد اكتمالها.


## تحقق Dashboard و25 رمزًا بعد الإصلاح الأخير

بعد تصحيح Environment الفعلية في Render وتطبيق commit `bd6625c` ثم إصلاح تحويل JSONB في `320d756` وإضافة Score التغطية في `1f63809`، أثبت التحقق الخارجي ما يلي:

| الفحص | النتيجة |
| --- | --- |
| `dashboard_symbols` | 25 |
| `worker_symbols` | 25 |
| `symbols_requested` في أحدث دورة مكتملة | 25 |
| `symbols_processed` | 25 |
| `decisions_count` | 25 |
| `error_count` | 0 |
| `diagnostic_symbol_count` | 25 |
| الرموز التي تحتوي 4 استراتيجيات | 25 |
| نوع `run.summary` في API | كائن JSON بعد إصلاح JSONB |
| Score التغطية | موجود لكل استراتيجية، مع `score_basis=condition_coverage` |
| Paper/live guard | `trading_mode=paper` و`live_trading_enabled=false` |

العينة الإنتاجية أظهرت مثلًا للاستراتيجية `TREND_PULLBACK`: تسعة شروط ناجحة من عشرة وScore تغطية `90/100`، ولـ`BREAKOUT_RETEST`: ستة من ثمانية وScore `75/100`. هذا Score هو تغطية الشروط التشخيصية، وليس Quality Score لمرشح مكتمل؛ لذلك يظل `quality_score` فارغًا عندما تكون حالة الرمز WATCH ولا يوجد setup مكتمل.

كما تم فحص HTML الإنتاجي وظهرت فيه عناوين `تشخيص كل رمز واستراتيجية` و`Score التغطية` و`السوق — 25 زوجًا` و`ملخص الدورات والتدقيق`. الاختبارات المحلية الأخيرة: `28 passed, 1 skipped`، وJavaScript syntax check ناجح.


## تدقيق الصورة والجرد التشغيلي اللاحق

العبارات السابقة التي تذكر أن Worker يعالج 3 رموزًا تصف حالة تاريخية قبل تعديل Environment في Render، وليست الحالة الحالية. آخر فحص خارجي أعاد `worker_symbols=25` و`worker_symbols_configured=25` و`worker_symbols_processed=25`، وآخر دورة مكتملة عالجت 25 رمزًا وأنشأت 25 قرارًا دون أخطاء.

الصورة المرفقة التُقطت أثناء دورة `RUNNING`. لذلك ظهر ملخص الدورة الجديدة بعداد غير مكتمل، بينما كانت بطاقة الحالة العامة تعرض عدادًا من دورة أو حالة أخرى. في RTL كانت صيغة `processed / requested` قابلة للانعكاس بصريًا، فبدت مثل `25 / 0` وأوحت بعطل. تم إصلاح ذلك في الواجهة بصياغة `تمت معالجة X من Y`، وأصبحت الواجهة تختار أحدث دورة `COMPLETED` لعرض Cycle Summary بدل اختيار أول سجل قد يكون `RUNNING`. كما تم تصفير عدادات الحالة المؤقتة عند بدء دورة جديدة لمنع بقاء بيانات الدورة السابقة أثناء التشغيل.

كشف فحص الأحداث أن آخر دورة مكتملة كانت سليمة من ناحية المحركات: `CANDLE_FETCH=100 SUCCESS` لأربعة أطر زمنية لكل 25 رمزًا، و`DECISION_EVALUATE=25 WATCH`، و`CANDLE_PERSIST=25 SUCCESS`، و`DECISION_PERSIST=25 SUCCESS`، و`REDIS_PUBLISH=25 SUCCESS`، مع `EVENT_ERRORS=[]`. سبب عدم إنشاء أوامر هو `SETUP_INCOMPLETE=25`، وليس فشل Worker أو Binance أو PostgreSQL أو Redis. متوسط تغطية الشروط لا يساوي احتمال صفقة؛ المرشح لا يصبح `ENTER` إلا بعد اكتمال استراتيجية، واجتياز الجودة والمخاطر وRR وEV وفق السياسة.

تم العثور على خلل Paper مخفي في مسار الإغلاق: Worker كان يفحص آخر شمعة مؤهلة فقط، لذلك يمكن أن يضيع وقف أو هدف ضُرب في شمعة أقدم بين دورتين ثم عاد السعر. تم إصلاح المسار ليعالج الشموع المغلقة المؤهلة زمنيًا ويغلق عند أول حدث، مع اختبار regression. كما تم إصلاح فك JSONB في `active_paper_position` و`recent_paper_orders`؛ كان payload قد يصل كسلسلة JSON، ما قد يمنع قراءة `risk_cash` عند الإغلاق أو يجعل API الصفقات أقل قابلية للاستخدام.

أصبحت الاختبارات المحلية بعد هذه الإصلاحات `31 passed, 1 skipped`، ونجح `compileall` وفحص JavaScript و`git diff --check`. لم تُرسل أوامر حقيقية، وما زال النظام Paper-only.
