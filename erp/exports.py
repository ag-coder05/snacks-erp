import csv
from io import BytesIO
from django.http import HttpResponse
from openpyxl import Workbook


def export_csv_response(filename: str, headers: list, rows: list) -> HttpResponse:
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}.csv"'
    writer = csv.writer(response)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    return response


def export_excel_response(filename: str, sheet_title: str, headers: list, rows: list) -> HttpResponse:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title[:31]
    ws.append(headers)
    for row in rows:
        ws.append([float(x) if hasattr(x, "quantize") else x for x in row])

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)

    response = HttpResponse(
        buf.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}.xlsx"'
    return response