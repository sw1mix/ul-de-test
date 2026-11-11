-- Проверка витрины: коридор Москва → Казань.
SELECT price_month,
       body_group,
       weight_category,
       round(p25_trip::numeric, 0) AS p25_trip,
       round(median_trip::numeric, 0) AS median_trip,
       round(p75_trip::numeric, 0) AS p75_trip,
       round(median_km::numeric, 2) AS median_km,
       trips_count,
       carriers_count,
       round(cv_rate::numeric, 3) AS cv_rate,
       round(mom_pct::numeric, 1) AS mom_pct,
       round(yoy_pct::numeric, 1) AS yoy_pct,
       reliability_level
FROM mart.route_card
WHERE loading_city = 'Москва'
  AND unloading_city = 'Казань'
ORDER BY price_month DESC, trips_count DESC
LIMIT 20;

-- Факт против прогноза на отложенном окне.
SELECT count(*) AS scored,
       round(avg(ape_pct)::numeric, 2) AS mape_pct,
       round(sqrt(avg(power(abs_error_rub, 2)))::numeric, 0) AS rmse
FROM mart.ml_price_predictions
WHERE actual_price_rub IS NOT NULL;

-- Будущие заявки: факта в сравнении нет.
SELECT count(*) AS future_rows
FROM mart.ml_price_predictions
WHERE loading_date > DATE '2026-10-26';
