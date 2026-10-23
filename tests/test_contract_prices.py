import pytest
from niba_test.contract_prices import read_contract_table, ContractTableError

# Synthetic prices and the observed interleaving of the two columns.
TABLE = '''TUS PRECIOS
Potencia (€/kW día) Energía (€/kWh)
Periodo Importe Periodo Importe
Potencia Punta (P1)
L-V 08:00 a 24:00 0,080 Energía Punta (P1)
L-V 10:00 a 14:00 y 18:00 a 22:00 0,200
Potencia Valle (P2)
L-V 00:00 a 8:00 S-D-F 00:00 a
24:00
0,060
Energía Llano (P2)
L-V 8:00 a 10:00, 14:00 a 18:00 y 22:00
a 00:00
0,150
Energía Valle (P3)
L-V 00:00 a 8:00 y S-D-F 00:00 a
24:00
0,090
¡ A estos precios se añaden: cuota del bono social (0.024688 €/día)'''

def test_two_column_table_and_arbitrary_rates():
    assert read_contract_table(TABLE) == dict(potencia_punta='0.080',potencia_valle='0.060',
                                             energia_punta='0.200',energia_llano='0.150',energia_valle='0.090')
    assert read_contract_table(TABLE.replace('0,090','0,123'))['energia_valle']=='0.123'

def test_line_breaks_do_not_matter():
    assert read_contract_table(TABLE)==read_contract_table(TABLE.replace('\n',' '))

@pytest.mark.parametrize('text',[TABLE.replace('0,080',''),TABLE.replace('0,080','0,080 0,070'),
                                 TABLE.replace('€/kW día','€/kW año'),TABLE+TABLE,
                                 TABLE.replace('Energía Valle (P3)','Energía Valle (P2)')])
def test_fail_closed(text):
    with pytest.raises(ContractTableError):read_contract_table(text)


def test_repeated_tables_and_conflicts():
    from niba_test.contract_prices import agree_tables, reconcile_contract_invoice
    prices = read_contract_table(TABLE)
    assert agree_tables([prices, dict(prices)]) == prices
    changed = dict(prices, energia_valle='0.123')
    with pytest.raises(ContractTableError): agree_tables([prices, changed])
    with pytest.raises(ContractTableError): agree_tables([])
    assert reconcile_contract_invoice(prices, dict(prices, iva='0.21'))['iva']=='0.21'
    with pytest.raises(ContractTableError): reconcile_contract_invoice(prices, changed)


def test_pdf_repeated_pages_and_missing_table(monkeypatch):
    from types import SimpleNamespace
    import pypdf
    from niba_test.contract_prices import read_contract_pdf
    pages = [SimpleNamespace(extract_text=lambda: TABLE),
             SimpleNamespace(extract_text=lambda: TABLE)]
    reader = SimpleNamespace(is_encrypted=False, pages=pages)
    monkeypatch.setattr(pypdf, 'PdfReader', lambda stream: reader)
    assert read_contract_pdf(b'fixture') == read_contract_table(TABLE)
    pages.append(SimpleNamespace(extract_text=lambda: TABLE.replace('0,090', '0,123')))
    with pytest.raises(ContractTableError):
        read_contract_pdf(b'fixture')
    reader.pages = [SimpleNamespace(extract_text=lambda: 'No economic table')]
    with pytest.raises(ContractTableError):
        read_contract_pdf(b'fixture')
