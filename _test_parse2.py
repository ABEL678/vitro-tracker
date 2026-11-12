from vitro.sharepoint import parse_path, GIS_WORK, ATP_WORK

# Проверим точные значения
print(f"ATP_WORK = {repr(ATP_WORK)}")
print(f"GIS_WORK = {repr(GIS_WORK)}")

# Тестовые пути
path_atp = "/okhta/DocProjectLib/01_РД/02_АТП/01_В процессе/02_КР/АТ-РД-КЖ1-1-00"
path_gis = "/okhta/DocProjectLib/01_РД/02_ГИС/01_В процессе/ГИС-РД-СБ-АСЗП-0-00"

print(f"\npath_atp.startswith(ATP_WORK + '/') = {path_atp.startswith(ATP_WORK + '/')}")
print(f"path_gis.startswith(GIS_WORK + '/') = {path_gis.startswith(GIS_WORK + '/')}")

# Пошагово разберём parse_path для ГИС
file_dir_ref = path_gis
work_prefixes = (ATP_WORK + "/", GIS_WORK + "/")
print(f"\nwork_prefixes = {work_prefixes}")
print(f"file_dir_ref = {repr(file_dir_ref)}")

for i, prefix in enumerate(work_prefixes):
    matches = file_dir_ref.startswith(prefix)
    print(f"  prefix[{i}] = {repr(prefix)} -> {matches}")
    if matches:
        rel = file_dir_ref[len(prefix):].strip("/")
        print(f"  rel = {repr(rel)}")
        parts = [p for p in rel.split("/") if p]
        print(f"  parts = {parts}")
        print(f"  len(parts) = {len(parts)}")
