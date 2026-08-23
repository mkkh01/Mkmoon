# Binance Spot WebSocket research

مصادر رسمية تمت مراجعتها في 23 أغسطس 2026:

1. https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/ws-streams/~ — Spot WebSocket Market Streams.
2. https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md — Official Spot WebSocket stream reference.

النتائج الأساسية من المصدر الأول: نقطة الاتصال العامة هي `wss://stream.binance.com:9443` أو المنفذ 443. يدعم Binance raw streams مثل `/ws/<stream>` وcombined streams مثل `/stream?streams=...` أو الاشتراك برسالة `SUBSCRIBE`. صفحة التوثيق تعرض Individual Symbol Mini Ticker Stream وAll Market Mini Tickers Stream، وتوضح أن mini ticker rolling 24-hour statistics ليست إحصاءات اليوم UTC. للاستخدام المطلوب، يمكن الاشتراك في 25 stream من نوع `<symbol>@miniTicker` داخل اتصال واحد، ثم تحديث السعر من الحقل `c` ووقت الحدث من `E`.

القرار الهندسي: استخدام اتصال واحد combined stream أو اتصال `/ws` مع رسالة SUBSCRIBE، مع reconnect بتدرج زمني، heartbeat، رفض الرسائل غير المعروفة، واعتبار السعر stale بعد مهلة محددة. WebSocket سيغذي أسعار Dashboard فقط. القرارات والصفقات تبقى في PostgreSQL، وأي تنفيذ يبقى Paper-only ولا تُستخدم Binance private credentials.


تفاصيل إضافية من المرجع الرسمي الثاني: stream endpoint هو `wss://stream.binance.com:9443` أو `wss://stream.binance.com:443`، ويمكن استخدام raw `/ws/<stream>` أو combined `/stream?streams=<stream1>/<stream2>`. أسماء الرموز في streams lowercase، ورسائل combined تُغلف في `{stream,data}`. الاتصال الواحد صالح 24 ساعة فقط، ويرسل الخادم ping frame كل 20 ثانية؛ يجب الرد pong بنسخة من payload خلال دقيقة. الحد الأقصى 5 رسائل واردة في الثانية، والاتصال الواحد يدعم حتى 1024 stream، مع حد 300 اتصال لكل محاولة خلال 5 دقائق لكل IP. يوجد أيضًا endpoint `wss://data-stream.binance.vision` لبيانات السوق فقط.

التصميم المختار: `data-stream.binance.vision` إن كان متاحًا عبر الشبكة، مع fallback إلى `stream.binance.com:443`. يستخدم العميل اتصالًا واحدًا لـ25 miniTicker streams، يرد تلقائيًا على ping عبر مكتبة WebSocket، يعيد الاتصال بتدرج زمني عند close/error، ويعيد إنشاء الاتصال دوريًا قبل حد 24 ساعة. الواجهة تعرض آخر قيمة مع `stale`/زمن آخر تحديث، ولا تستخدم stream لإرسال أوامر.


فحص نشر WebSocket على `https://mkmoon.onrender.com/?wscheck=1`: deployment الأخير live، والصفحة تعرض الخدمة Online، PostgreSQL Connected، وRedis Connected، مع Paper-only وlive=false. عند فتح Dashboard ظهر `WebSocket · جارٍ الاتصال`، وبعد الانتظار لم تتحول الحالة إلى متصل ولم تظهر صفوف الأسعار؛ يلزم فحص console/network لتحديد سبب فشل اتصال المتصفح بـ`stream.binance.com:443` أو إضافة fallback عملي.
