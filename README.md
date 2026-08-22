# Mkmoon — Binance Spot Decision Engine

محرك قرار تداول **Rule-Based** للعملات الرقمية على Binance Spot، مصمم ليكون حتميًا وقابلًا للتدقيق وإعادة التشغيل. الإصدار الحالي **Paper-first**: يجلب بيانات السوق العامة، يستخدم الشموع المغلقة فقط، يحسب Regime وSetups وScore والمخاطر، ويحفظ القرارات في Supabase وينشرها عبر Redis. لا يرسل أوامر حقيقية افتراضيًا.

## البنية

- `app/adapters/binance_public.py`: موصل Binance العام لـ`serverTime`, `exchangeInfo`, وclosed klines.
- `app/adapters/binance_private.py`: موصل خاص مستقبلي، محمي بحاجز يمنع الأوامر خارج Live mode الصريح.
- `app/engine/`: Features، Regime، Setups، Scoring، Risk، Decision، Replay، وPaper simulator.
- `app/storage/`: PostgreSQL/Supabase وRedis.
- `app/main.py`: FastAPI API على Render.
- `app/worker.py`: عامل Paper دوري على Render.
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

## Render

ملف `render.yaml` يعرّف خدمتين: Web API وBackground Worker. اضبط الأسرار من لوحة Render فقط:

- `DATABASE_URL`: رابط PostgreSQL الخاص بـSupabase.
- `REDIS_URL`: رابط Redis.
- `BINANCE_API_KEY` و`BINANCE_API_SECRET`: لا يلزمان للـPaper market-data mode، ولا يضافان إلا بعد فتح مرحلة Live صراحةً.
- `LIVE_TRADING_ENABLED=false` و`TRADING_MODE=paper` يجب أن يبقيا كما هما أثناء الاختبارات.

## قاعدة البيانات

تمت إضافة migration `0001_trading_core.sql` إلى مشروع Supabase المحدد، ثم تطبيق `0002_security_hardening.sql`. الجداول الداخلية مفعّل عليها RLS ولا توجد لها سياسات عامة، كما تم سحب صلاحية `anon` و`authenticated` من دالة `public.rls_auto_enable()` ومن الجداول الداخلية. خدمة Render تتصل عبر رابط PostgreSQL الخاص.

## قواعد الأمان

لا تضع أسرار Binance أو Supabase أو Redis في Git أو logs. لا تستخدم API key بصلاحية سحب الأموال. لا تفتح `LIVE_TRADING_ENABLED` قبل نجاح Replay وBacktest وPaper وShadow، وقياس drift، ومراجعة يدوية مستقلة. أي HTTP 5XX أثناء طلب أمر خاص يجب اعتباره **حالة تنفيذ غير معروفة** ويتطلب reconciliation قبل إعادة المحاولة.

## حدود الإصدار الحالي

الإصدار الحالي ليس نظامًا يثبت الربحية ولا يرسل أوامر حقيقية. ما يزال يلزم قبل Live: تثبيت بروتوكول قبول رقمي، توسيع تنفيذ Position/Order lifecycle، إضافة WebSocket reconciliation، تفعيل account snapshots وrisk reservations الذرية، وبناء Backtest كامل ببيانات تاريخية وسياسة purge/embargo وFinal Test مقفلة.
