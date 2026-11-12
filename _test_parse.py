from vitro.sharepoint import parse_path, GIS_WORK

# Тест с путём АТП (должен работать)
result_atp = parse_path("/okhta/DocProjectLib/01_РД/02_АТП/01_В процессе/02_КР/АТ-РД-КЖ1-1-00")
print(f"АТП: {result_atp}")

# Тест с путём ГИС
result_gis = parse_path("/okhta/DocProjectLib/01_РД/02_ГИС/01_В процессе/ГИС-РД-СБ-АСЗП-0-00")
print(f"ГИС: {result_gis}")

# Проверим константу
print(f"\nGIS_WORK = {GIS_WORK}")

# Проверим startswith
test_path = "/okhta/DocProjectLib/01_РД/02_ГИС/01_В процессе/ГИС-РД-СБ-АСЗП-0-00"
print(f"\ntest_path.startswith(GIS_WORK + '/') = {test_path.startswith(GIS_WORK + '/')}")
