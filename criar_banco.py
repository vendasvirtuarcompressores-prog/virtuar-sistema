import os
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path
import shutil

# Configuração de caminhos
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("VIRTUAR_DATA_DIR", Path.home() / "VirtuArData")).expanduser()
DB_PATH = DATA_DIR / "compras_nfe.db"
PASTA_XMLS = DATA_DIR / "xmls"


def migrar_dados_legados():
    """Move os dados antigos para a pasta persistente sem sobrescrever uploads."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    banco_antigo = BASE_DIR / "compras_nfe.db"
    if banco_antigo.exists() and not DB_PATH.exists():
        shutil.copy2(banco_antigo, DB_PATH)

    xmls_antigos = BASE_DIR / "xmls"
    if xmls_antigos.exists():
        PASTA_XMLS.mkdir(parents=True, exist_ok=True)
        for arquivo in xmls_antigos.glob("*.xml"):
            destino = PASTA_XMLS / arquivo.name
            if not destino.exists():
                shutil.copy2(arquivo, destino)


migrar_dados_legados()


def inicializar_banco():
    """Cria a estrutura das tabelas no SQLite caso não existam."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Tabela de Fornecedores
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS fornecedores (
        cnpj TEXT PRIMARY KEY,
        nome TEXT,
        uf TEXT
    )
    """)

    # Tabela de Notas Fiscais
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS notas_fiscais (
        chave_nfe TEXT PRIMARY KEY,
        numero_nf TEXT,
        data_emissao TEXT,
        cnpj_fornecedor TEXT,
        valor_total REAL,
        FOREIGN KEY (cnpj_fornecedor) REFERENCES fornecedores (cnpj)
    )
    """)

    # Tabela de Itens da Nota
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS itens_nota (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chave_nfe TEXT,
        codigo_prod TEXT,
        descricao TEXT,
        ean TEXT,
        ncm TEXT,
        quantidade REAL,
        valor_unitario REAL,
        valor_total REAL,
        FOREIGN KEY (chave_nfe) REFERENCES notas_fiscais (chave_nfe)
    )
    """)

    # NOVA TABELA: Clientes para as Cotações
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS clientes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cnpj_cpf TEXT UNIQUE,
        razao_social TEXT,
        telefone TEXT,
        endereco TEXT,
        cidade_uf TEXT,
        cep TEXT,
        data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()
    conn.close()


def processar_xml(caminho_xml):
    """Lê um arquivo XML de NF-e e extrai os dados."""
    tree = ET.parse(caminho_xml)
    root = tree.getroot()

    ns = {"nfe": "http://www.portalfiscal.inf.br/nfe"}

    inf_nfe = root.find(".//nfe:infNFe", ns)
    if inf_nfe is None:
        return None

    chave_nfe = inf_nfe.attrib.get("Id", "").replace("NFe", "")

    ide = inf_nfe.find("nfe:ide", ns)
    numero_nf = ide.findtext("nfe:nNF", "", ns)
    data_emissao = ide.findtext("nfe:dhEmi", "", ns)[:10]

    emit = inf_nfe.find("nfe:emit", ns)
    cnpj_fornecedor = emit.findtext("nfe:CNPJ", "", ns)
    nome_fornecedor = emit.findtext("nfe:xNome", "", ns)
    uf_fornecedor = emit.find("nfe:enderEmit", ns).findtext("nfe:UF", "", ns)

    total = inf_nfe.find(".//nfe:ICMSTot", ns)
    valor_total_nf = float(total.findtext("nfe:vNF", "0.0", ns))

    itens = []
    for det in inf_nfe.findall("nfe:det", ns):
        prod = det.find("nfe:prod", ns)
        itens.append(
            {
                "codigo_prod": prod.findtext("nfe:cProd", "", ns),
                "descricao": prod.findtext("nfe:xProd", "", ns),
                "ean": prod.findtext("nfe:cEAN", "", ns),
                "ncm": prod.findtext("nfe:NCM", "", ns),
                "quantidade": float(prod.findtext("nfe:qCom", "0.0", ns)),
                "valor_unitario": float(prod.findtext("nfe:vUnCom", "0.0", ns)),
                "valor_total": float(prod.findtext("nfe:vProd", "0.0", ns)),
            }
        )

    return {
        "chave_nfe": chave_nfe,
        "numero_nf": numero_nf,
        "data_emissao": data_emissao,
        "fornecedor": {
            "cnpj": cnpj_fornecedor,
            "nome": nome_fornecedor,
            "uf": uf_fornecedor,
        },
        "valor_total_nf": valor_total_nf,
        "itens": itens,
    }


def salvar_no_banco(dados):
    """Insere ou atualiza os dados no banco de dados SQLite."""
    if not dados:
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT OR IGNORE INTO fornecedores (cnpj, nome, uf)
        VALUES (?, ?, ?)
    """,
        (
            dados["fornecedor"]["cnpj"],
            dados["fornecedor"]["nome"],
            dados["fornecedor"]["uf"],
        ),
    )

    cursor.execute(
        """
        INSERT OR REPLACE INTO notas_fiscais (chave_nfe, numero_nf, data_emissao, cnpj_fornecedor, valor_total)
        VALUES (?, ?, ?, ?, ?)
    """,
        (
            dados["chave_nfe"],
            dados["numero_nf"],
            dados["data_emissao"],
            dados["fornecedor"]["cnpj"],
            dados["valor_total_nf"],
        ),
    )

    cursor.execute(
        "DELETE FROM itens_nota WHERE chave_nfe = ?", (dados["chave_nfe"],)
    )

    for item in dados["itens"]:
        cursor.execute(
            """
            INSERT INTO itens_nota (chave_nfe, codigo_prod, descricao, ean, ncm, quantidade, valor_unitario, valor_total)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                dados["chave_nfe"],
                item["codigo_prod"],
                item["descricao"],
                item["ean"],
                item["ncm"],
                item["quantidade"],
                item["valor_unitario"],
                item["valor_total"],
            ),
        )

    conn.commit()
    conn.close()


def importar_todos_xmls():
    """Percorre a pasta de XMLs e importa um por um."""
    inicializar_banco()

    if not PASTA_XMLS.exists():
        PASTA_XMLS.mkdir(parents=True, exist_ok=True)
        return 0, 0

    arquivos = list(PASTA_XMLS.glob("*.xml"))
    sucessos = 0
    erros = 0

    for arquivo in arquivos:
        try:
            dados = processar_xml(arquivo)
            if dados:
                salvar_no_banco(dados)
                sucessos += 1
        except Exception:
            erros += 1

    return sucessos, erros


def salvar_xml_upload(arquivo):
    """
    Salva o XML enviado pelo Streamlit na pasta permanente
    e retorna o caminho do arquivo salvo.
    """
    PASTA_XMLS.mkdir(parents=True, exist_ok=True)
    nome_arquivo = Path(arquivo.name).name
    caminho_xml = PASTA_XMLS / nome_arquivo

    if not caminho_xml.exists():
        caminho_xml.write_bytes(arquivo.getvalue())

    return caminho_xml


if __name__ == "__main__":
    importar_todos_xmls()