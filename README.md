# Mkmoon — Binance Spot Decision Engine

محرك قرار تداول **Rule-Based** للعملات الرقمية على Binance Spot، مصمم ليكون حتميًا وقابلًا للتدقيق وإعادة التشغيل. الإصدار الحالي **Paper-first**: يجلب بيانات السوق العامة، يستخدم الشموع المغلقة فقط، يحسب Regime وSetups وScore والمخاطر، ويحفظ القرارات في Supabase وينشرها عبر Redis. لا يرسل أوامر حقيقية افتراضيًا.

## البنية

- `app/adapters/binance_public.py`: موصل Binance العام لـ`serverTime`, `exchangeInfo`, وclosed klines.
- `app/adapters/binance_private.py`: موصل خاص مستقبلي، محمي بحاجز يمنع الأوامر خارج Live mode الصريح.
- `app/engine/`: Features، Regime، Setups، Scoring، Risk، Decision، Replay، وPaper simulator.
- `app/storage/`: PostgreSQL/Supabase وRedis.
- `app/main.py`: FastAPI API وDashboard على Render.
- `app/static/index.html`: لوحة مراقبة عربية متجاوبة للحالة، السوق، القرارات، الصفقات الورقية، وسجل التطبيق.
- `app/worker.py`: عامل Paper دوري مضمّن داخل نفس Render Web Service عبر FastAPI lifespan؛ لا توجد خدمة Background Worker منفصلة في النشر الحالي.
- `migrations/`: جداول الشموع والقرارات والأحداث والأوامر الافتراضية وحجوزات المخاطر.
- `tests/`: اختبارات الحتمية، المخاطر، Replay، وPaper execution.

## التشغيل المحلي

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
cp .env.example .env
pytest -q
uvicorn app.main:app --reload
```

لتشغيل العامل محليًا:

```bash
python -m app.worker
```

## Dashboard

عند تشغيل الخدمة، يفتح الرابط الأساسي صفحة Dashboard: `https://mkmoon.onrender.com/`. تعرض الصفحة حالة الخدمة وPostgreSQL وRedis، وأسعار 25 زوجًا من Binance USDT عبر بيانات عامة، وسجل القرارات، والصفقات الورقية المفتوحة والمغلقة، وسجل التطبيق مع البحث والتصفية. المسارات البرمجية المقابلة هي `/api/dashboard/summary` و`/api/market/tickers` و`/api/decisions` و`/api/paper-orders` و`/api/logs`.

الأسعار مصدرها Binance public market data فقط، ولا تُستخدم لإرسال أوامر. تستخدم Dashboard اتصال WebSocket واحدًا لـ25 miniTicker streams للتحديث اللحظي، مع reconnect وheartbeat ودوران دوري قبل حد Binance، وتستخدم REST fallback عند الانقطاع. وإذا رفضت شبكة Render طلبات Binance العامة، تستخدم الواجهة fallback مباشرًا من متصفح المستخدم. سجلات Render الكاملة تبقى في صفحة Logs داخل Render؛ أما `/api/logs` فيعرض سجل التطبيق الذي تجمعه الخدمة أثناء تشغيلها، مع إزالة عناوين الاتصال الحساسة من الرسائل.

## Cycle Summary والتدقيق التشغيلي

ينشئ Paper Worker سجلًا دائمًا لكل دورة في `cycle_runs`، ويسجل مراحلها التفصيلية في `cycle_events`. يبدأ السجل من `CYCLE_START` واتصالات PostgreSQL وRedis، ثم يتحقق من وقت Binance ومعلومات الأزواج، ويتتبع لكل رمز جلب الشموع المغلقة لكل timeframe، وبوابة سلامة البيانات والميزات، وتقييم القرار مع `status` و`reason_codes` وquality وEV، ثم حفظ الشموع والقرار، نشر Redis، وأخيرًا نتيجة Paper execution. تُحفظ أيضًا مدد المراحل، أعداد الشموع، آخر cutoff، عدد الرموز والقرارات والأخطاء، أكثر أسباب عدم التداول، وإصدارات الكود والإعدادات. الحالة `COMPLETED` تعني اكتمال الدورة بلا أخطاء أو رموز متخطاة، و`PARTIAL` تعني أن الدورة عالجت بعض البيانات مع وجود نقص أو خطأ، و`FAILED` تعني عدم إنتاج قرارات قابلة للاستخدام. تبويب `ملخص الدورات` في Dashboard يدعم البحث وفتح timeline كامل لأي دورة، ما يسمح بالتمييز بين خلل Binance، نقص شموع، رفض setup، حدود المخاطر، فشل التخزين، فشل Redis، أو حظر التنفيذ الورقي. لا يسجل هذا المسار أسرار الاتصال ولا يرسل أوامر حية.

## Render

يعمل Paper Worker داخل Web Service نفسه عندما تكون `TRADING_MODE=paper` و`LIVE_TRADING_ENABLED=false`، عبر مهمة `asyncio` تبدأ بعد إقلاع FastAPI وتتوقف عند إغلاقه. لذلك لا يحتاج التشغيل الحالي إلى Background Worker مدفوع أو خدمة Render ثانية. على الخطة المجانية قد يوقف Render الخدمة بعد الخمول أو يعيد تشغيل العملية؛ عند كل إقلاع تبدأ دورة جديدة، وتبقى Cycle Summary هي المرجع لاكتشاف الدورات المتوقفة أو غير المكتملة. اضبط الأسرار من لوحة Render فقط:

- `DATABASE_URL`: رابط PostgreSQL الخاص بـSupabase.
- `REDIS_URL`: رابط Redis.
- `BINANCE_API_KEY` و`BINANCE_API_SECRET`: لا يلزمان للـPaper market-data mode، ولا يضافان إلا بعد فتح مرحلة Live صراحةً.
- `LIVE_TRADING_ENABLED=false` و`TRADING_MODE=paper` يجب أن يبقيا كما هما أثناء الاختبارات.

## قاعدة البيانات

تمت إضافة migrations `0001_trading_core.sql` و`0002_security_hardening.sql` و`0003_revoke_public_function.sql` و`0004_execution_audit_entities.sql` إلى مشروع Supabase المحدد وتطبيقها. الجداول الداخلية مفعّل عليها RLS ولا توجد لها سياسات عامة، كما تم سحب صلاحية `PUBLIC` من دالة `public.rls_auto_enable()` ومن الجداول الداخلية. خدمة Render تتصل عبر رابط PostgreSQL الخاص.

## قواعد الأمان

لا تضع أسرار Binance أو Supabase أو Redis في Git أو logs. لا تستخدم API key بصلاحية سحب الأموال. لا تفتح `LIVE_TRADING_ENABLED` قبل نجاح Replay وBacktest وPaper وShadow، وقياس drift، ومراجعة يدوية مستقلة. أي HTTP 5XX أثناء طلب أمر خاص يجب اعتباره **حالة تنفيذ غير معروفة** ويتطلب reconciliation قبل إعادة المحاولة.

## حدود الإصدار الحالي

الإصدار الحالي ليس نظامًا يثبت الربحية ولا يرسل أوامر حقيقية. Paper account وPaper positions وrisk reservations الذرية ومسار الإغلاق الورقي موجودة للتجربة والتدقيق؛ وما يزال يلزم قبل Live: بروتوكول قبول رقمي، تنفيذ Live Position/Order lifecycle كامل، WebSocket reconciliation، account snapshots لحساب حقيقي، وبناء Backtest كامل ببيانات تاريخية وسياسة purge/embargo وFinal Test مقفلة.
