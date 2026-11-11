-- Маркер конца инициализации. Healthcheck ждёт эту таблицу,
-- потому что pg_isready отвечает ещё пока идёт synthetic.sql.
CREATE TABLE public.source_ready (
    ready boolean NOT NULL
);
INSERT INTO public.source_ready VALUES (true);
