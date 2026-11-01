import csv
from decimal import Decimal
import io
import json
from pathlib import Path
import sys
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import table_export

NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def row(**extra):
    return {'id':'a','title':'莫斯科房源','url':'https://www.cian.ru/rent/flat/123/?token=PRIVATE','district':'Лианозово','address':'Москва, Лесная улица, 12','metro':'Сокол','rooms':2,'area':'52.5','floor':14,'floors':17,'price':'80000.50','pricePeriod':'month','priceCurrency':'RUB','deposit':40000,'depositCurrency':'RUB','commission':'50','commissionCurrency':'RUB','status':'ready',**extra}


class TableExportTests(unittest.TestCase):
    def test_real_xlsx_zip_structure_typed_values_styles_and_no_external_relationships(self):
        content,mime,name=table_export.export_table({'format':'xlsx','rows':[row()]})
        self.assertTrue(content.startswith(b'PK'))
        self.assertIn('spreadsheetml.sheet',mime)
        self.assertTrue(name.endswith('.xlsx'))
        with ZipFile(io.BytesIO(content)) as archive:
            expected={'[Content_Types].xml','_rels/.rels','xl/workbook.xml','xl/_rels/workbook.xml.rels','xl/styles.xml','xl/worksheets/sheet1.xml'}
            self.assertEqual(set(archive.namelist()),expected)
            for path in archive.namelist():
                text=archive.read(path).decode()
                ET.fromstring(text)
                self.assertNotIn('TargetMode="External"',text)
                self.assertNotIn('PRIVATE',text)
            sheet=ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
            self.assertEqual(sheet.find('s:sheetViews/s:sheetView/s:pane',NS).get('state'),'frozen')
            self.assertEqual(sheet.find('s:autoFilter',NS).get('ref'),'A4:X5')
            cells={cell.get('r'):cell for cell in sheet.findall('.//s:c',NS)}
            self.assertEqual(cells['K5'].find('s:v',NS).text,'80000.50')
            self.assertEqual(cells['V5'].find('s:v',NS).text,'160000.75')
            self.assertEqual(cells['J5'].find('s:v',NS).text,'17')
            self.assertFalse(sheet.findall('.//s:f',NS))
            styles=ET.fromstring(archive.read('xl/styles.xml'))
            formats=styles.find('s:cellXfs',NS)
            for cell in cells.values():
                self.assertLess(int(cell.get('s','0')),len(formats))

    def test_export_has_no_private_field_channel_and_rejects_unknown_objects(self):
        for key in ('source','sourceHtml','contracts','phones','notes','workflow','cookies','body'):
            with self.subTest(key=key),self.assertRaises(ValueError):
                table_export.export_table({'format':'xlsx','rows':[row(**{key:'PRIVATE'})]})
        with self.assertRaises(ValueError):table_export.export_table({'format':'xlsx','rows':[row(),row()]})
        with self.assertRaises(ValueError):table_export.export_table({'format':'xlsx','rows':[row()]*5001})

    def test_csv_escapes_formulas_and_quotes_unicode_without_guessing_missing_values(self):
        for title in ('=HYPERLINK("https://bad.example")',' +CMD','-cmd','@SUM(1)','\t=1+1','\u200b=1+1'):
            with self.subTest(title=title):
                content,_,_=table_export.export_table({'format':'csv','rows':[row(title=title,deposit=0,commission=0)]})
                records=list(csv.reader(io.StringIO(content.decode('utf-8-sig')),delimiter=';'))
                self.assertEqual(records[1][1],"'"+title)
                self.assertEqual(records[1][13],'0')
        content,_,_=table_export.export_table({'format':'csv','rows':[row(title='房源; "厨房" +7 (999) 123-45-67 和 +86 13812345678',deposit=None,commission=None)]})
        parsed=list(csv.reader(io.StringIO(content.decode('utf-8-sig')),delimiter=';'))
        self.assertIn('房源; "厨房"',parsed[1][1]);self.assertNotIn('999)',parsed[1][1]);self.assertNotIn('13812345678',parsed[1][1])
        self.assertEqual(parsed[1][13],'');self.assertEqual(parsed[1][15],'');self.assertEqual(parsed[1][21],'')

    def test_formula_looking_strings_are_inline_text_in_xlsx_never_formulas(self):
        content,_,_=table_export.export_table({'format':'xlsx','rows':[row(title='=1+1',address='@SUM(1)')]})
        with ZipFile(io.BytesIO(content)) as archive:
            sheet=ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
        cell=sheet.find('.//s:c[@r="B5"]',NS)
        self.assertEqual(cell.get('t'),'inlineStr');self.assertEqual(cell.find('s:is/s:t',NS).text,'=1+1')
        self.assertFalse(sheet.findall('.//s:f',NS))

    def test_unknown_daily_foreign_or_conflicting_conditions_do_not_produce_full_ruble_total(self):
        for extra in ({'pricePeriod':'day'},{'priceCurrency':'unknown'},{'depositCurrency':'USD'},{'commission':None,'commissionAmount':100,'commissionCurrency':'USD'},{'commission':50,'commissionAmount':100}):
            with self.subTest(extra=extra):
                result=table_export.initial_payment(table_export.normalize_row(row(**extra)))
                self.assertIsNone(result['firstTotal']);self.assertEqual(result['paymentStatus'],'Требует уточнения')
        result=table_export.initial_payment(table_export.normalize_row(row(deposit=1000,depositCurrency='USD',commission=None,commissionAmount=100,commissionCurrency='USD')))
        self.assertEqual(result['firstSubtotal'],Decimal('80000.50'))

    def test_numeric_and_array_bounds_are_strict(self):
        for extra in ({'price':True},{'deposit':-1},{'area':'NaN'},{'commission':1001},{'rooms':1.5},{'prepayMonths':0},{'priceCurrency':'CNY'},{'oneTimeFees':[{'label':'a','amount':1,'source':'PRIVATE'}]}):
            with self.subTest(extra=extra),self.assertRaises(ValueError):table_export.normalize_row(row(**extra))

    def test_book_can_be_read_by_independent_reader_when_available(self):
        try:
            import openpyxl
        except ImportError:
            self.skipTest('Independent reader is optional; no new dependency installed')
        content,_,_=table_export.export_table({'format':'xlsx','rows':[row(commission='33.3333')]})
        workbook=openpyxl.load_workbook(io.BytesIO(content),data_only=True)
        self.assertEqual(workbook.sheetnames,['Объекты']);sheet=workbook['Объекты']
        self.assertEqual(sheet['B5'].value,'莫斯科房源');self.assertEqual(sheet['P5'].value,33.3333);self.assertEqual(sheet['J5'].value,17)
        self.assertEqual(sheet.freeze_panes,'A5')


if __name__=='__main__':unittest.main()
