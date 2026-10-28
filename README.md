# Тестовое: постобработка OCR паспорта РФ

```bash
python postprocess.py data/test_predictions.jsonl answers.jsonl
```

`postprocess.py` использует только стандартную библиотеку Python и справочник `reference/subdivisions.csv`. Итоговый файл `answers.jsonl` уже создан. `analysis.ipynb` содержит сравнение вариантов и был выполнен. Отчет для работодателя — `report.pdf` (исходник `report_ru.md`).

Папка `data/` не входит в комплект для публикации. Для повторного запуска положите в нее файлы из тестового задания. Готовый `answers.jsonl` уже включен.

Чтобы воспроизвести проверку train:

```bash
python postprocess.py data/train_predictions.jsonl /tmp/train_answers.jsonl
python evaluate.py /tmp/train_answers.jsonl data/train_labels.jsonl
```

Результат: 139 верных приемов, 0 ошибок по полям на train. Функция `valid_on_date` отдельно проверяет действительность паспорта. На test принято 83 документа. Выбор порогов сделан по train, поэтому это не гарантия отсутствия ошибок на test.

Справочник кодов: DaData / hflabs, CC BY-SA 4.0, подробности в `reference/SOURCE.txt`.
