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
