# Mkmoon Binance Spot — Implementation Specification v1

## 1. حدود الإصدار

هذا الإصدار **Spot / LONG-only / Signal + Paper**. لا يستخدم Futures أو Leverage أو سحب الأموال، ولا يرسل أوامر Binance حقيقية. الكون هو قائمة رموز صريحة في `SYMBOLS`، وليست قائمة تُختار من بيانات مستقبلية. أي توسيع للكون يحتاج version جديد ونتائج مستقلة.

## 2. مرجعية الزمن

كل timestamps الداخلية بالـmilliseconds وUTC. لا تدخل شمعة إلى Feature أو Decision إلا إذا كان `close_time_ms <= decision_time_ms` و`is_closed=true`. `data_cutoff_ms` هو أصغر `close_time_ms` بين الأطر المستخدمة، وتُحفظ جميع أزمنة آخر شمعة لكل timeframe في snapshot عند إضافة نظام snapshots الكامل.

الإصدار الحالي يستخدم REST closed-klines كل دورة. أي WebSocket لاحقًا يجب أن يعيد المزامنة عبر REST بعد الاتصال أو الانقطاع، ولا يجوز استئناف إشارات جديدة من stream غير مُصالح عليه.

## 3. Binance Contract

يُقرأ `exchangeInfo` ويُحفظ versioned. قبل أي أمر مستقبلي يجب تطبيق `PRICE_FILTER`, `LOT_SIZE`, `MARKET_LOT_SIZE`, `MIN_NOTIONAL`/`NOTIONAL`, percent-price، وحدود عدد الأوامر. يستخدم الحساب `Decimal` فقط، ويُقرب quantity إلى أسفل، ثم يعاد حساب المخاطر والقيمة الاسمية.

أي طلب خاص يستخدم clock offset مع Binance server time و`recvWindow <= 60000`. HTTP 429 يؤدي إلى backoff واحترام `Retry-After`. HTTP 5XX في طلب غير idempotent يعامل كـ`UNKNOWN_EXECUTION_STATUS` ولا يعاد إرساله قبل الاستعلام عن حالة الأمر.

## 4. Regime Vector

الأبعاد الرسمية هي: `trend_direction`, `trend_strength`, `volatility_phase`, `structure_mode`, `market_safety`, `liquidity_state`, و`reversal_status`. لا يجوز إسقاط بُعد في Signal schema. العتبات في `configs/config.v1.yaml`، وتغييرها ينشئ config version جديدًا.

## 5. Score

العوامل الرسمية عشرة، وأوزانها في v1 مجموعها 100: regime، MTF، structure، liquidity، zone، volume، momentum، volatility، timing، setup_quality. كل عامل من 0 إلى 100، والتحويل النهائي:

```text
score = round_half_up(sum(weight_i / 100 * factor_i), 2)
```

الـScore مقياس جودة مركب وليس probability. فشل Gate لا يمكن تعويضه بالـScore، والعامل غير المتاح لا يتحول إلى صفر أو قيمة محايدة بصمت.

## 6. Setups

كل Setup يعيد `entry_price`, `stop_price`, `target_price`, `setup_type`, وcomponent scores. لا يعاد استخدام Setup نفسه لنفس zone lifecycle. في v1 توجد أربعة أنواع، لكن الدخول يتطلب أيضًا بيانات صحيحة، سوقًا آمنًا، trigger مغلقًا، risk صالحًا، وeffective RR فوق الحد.

التعريفات الحالية هي baseline تنفيذي وليست دليل ربحية. يجب تقييم كل Setup مستقلًا، بعد التكاليف، مقابل baseline بسيط وخارج العينة.

## 7. Risk Contract

```text
risk_cash = min(
  equity * risk_pct,
  remaining_daily_risk,
  remaining_portfolio_risk,
  symbol_risk_cap,
  cluster_risk_cap
)
unit_risk = abs(entry - stop) + expected_cost_per_unit
quantity = floor_to_step(risk_cash / unit_risk, step_size)
effective_risk = quantity * unit_risk
```

إذا تجاوزت `effective_risk` السقف بعد التقريب، أو فشلت minimum quantity/notional، فالقرار مرفوض. في الإنتاج يجب أن تكون حجوزات المخاطر atomic ومتصلة بـaccount snapshot؛ قيم paper الافتراضية ليست قياسًا لحساب حقيقي.

## 8. Decision Gate

الترتيب الإلزامي هو: Data → Asset → Market Safety → Setup → Timing → Conflict → Score → Risk → Exposure → Execution Feasibility → EV policy → Ranking. نتيجة `INSUFFICIENT_DATA` لا تسمح بالدخول إلا عبر fallback إحصائي versioned ومُختبر؛ الإعداد الافتراضي في الإصدار الأول هو عدم إصدار أمر حقيقي.

## 9. Replay and Hash

يُبنى hash من canonical JSON مرتب المفاتيح، مع تمثيل Decimal نصيًا، ويشمل data snapshot، cutoff، versions، inputs، output status، وreason codes. يجب أن يعيد نفس snapshot نفس hash ونفس `created_at` المشتق من decision time.

## 10. Paper Execution

في حال اجتماع SL وTP في شمعة واحدة دون intrabar data، يستخدم Paper محاكاة محافظة تفترض SL أولًا في LONG. هذه قاعدة محاكاة وليست ادعاءً عن ترتيب السوق الحقيقي. يوسم كل fill بمصدره وقاعدة التنفيذ المستخدمة.

## 11. Release Gates

لا ينتقل النظام من Paper إلى Shadow إلا بعد نجاح compile/tests، Replay parity، leakage tests، filter tests، risk rounding tests، وقياس drift بين Backtest وPaper. لا ينتقل إلى Live إلا بعد: مفاتيح Binance منفصلة محدودة الصلاحيات، reconciliation، kill switch، مراقبة، runbooks، وموافقة تشغيلية مستقلة. لا تثبت هذه المواصفة الربحية؛ الإثبات الوحيد الممكن هو نتائج موثقة وفق بروتوكول تاريخي ثابت وخارج العينة.
