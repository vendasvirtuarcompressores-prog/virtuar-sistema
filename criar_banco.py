import os
import xml.etree.ElementTree as ET
from pathlib import Path

import db

# ---------------------------------------------------------------------------
# CAMINHOS PARA OS ARQUIVOS XML (o banco em si é controlado pelo db.py)
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("VIRTUAR_DATA_DIR", Path.home() / "VirtuArData")).expanduser()
PASTA_XMLS = DATA_DIR / "xmls"

# Mantido por compatibilidade com código antigo que possa importar DB_PATH.
DB_PATH = DATA_DIR / "compras_nfe.db"


def inicializar_banco():
    """Cria a estrutura das tabelas (Postgres ou SQLite, conforme configurado)."""
    db.init_schema()


try:
    inicializar_banco()
except Exception as _erro_init:
    print(f"[VirtuAr] Aviso: não foi possível inicializar o banco: {_erro_init}")


def processar_xml(caminho_xml):
    """Lê um arquivo XML de NF-e e extrai os dados. Retorna None se não for NF-e."""
    tree = ET.parse(caminho_xml)
    root = tree.getroot()
    ns = {"nfe": "http://www.portalfiscal.inf.br/nfe"}

    inf_nfe = root.find(".//nfe:infNFe", ns)
    if inf_nfe is None:
        return None

    chave_nfe = inf_nfe.attrib.get("Id", "").replace("NFe", "")

    ide = inf_nfe.find("nfe:ide", ns)
    numero_nf = ide.findtext("nfe:nNF", "", ns) if ide is not None else ""
    data_emissao = (
        (ide.findtext("nfe:dhEmi", "", ns) or ide.findtext("nfe:dEmi", "", ns) or "")[:10]
        if ide is not None else ""
    )

    emit = inf_nfe.find("nfe:emit", ns)
    cnpj_fornecedor = emit.findtext("nfe:CNPJ", "", ns) if emit is not None else ""
    nome_fornecedor = emit.findtext("nfe:xNome", "", ns) if emit is not None else ""

    ender_emit = emit.find("nfe:enderEmit", ns) if emit is not None else None
    uf_fornecedor = ender_emit.findtext("nfe:UF", "", ns) if ender_emit is not None else ""

    total = inf_nfe.find(".//nfe:ICMSTot", ns)
    valor_total_nf = float(total.findtext("nfe:vNF", "0.0", ns)) if total is not None else 0.0

    itens = []
    for det in inf_nfe.findall("nfe:det", ns):
        prod = det.find("nfe:prod", ns)
        if prod is None:
            continue
        itens.append(
            {
                "codigo_prod": (prod.findtext("nfe:cProd", "", ns) or "").strip(),
                "descricao": (prod.findtext("nfe:xProd", "", ns) or "").strip(),
                "ean": (prod.findtext("nfe:cEAN", "", ns) or "").strip(),
                "ncm": (prod.findtext("nfe:NCM", "", ns) or "").strip(),
                "quantidade": float(prod.findtext("nfe:qCom", "0.0", ns) or 0),
                "valor_unitario": float(prod.findtext("nfe:vUnCom", "0.0", ns) or 0),
                "valor_total": float(prod.findtext("nfe:vProd", "0.0", ns) or 0),
            }
        )

    return {
        "chave_nfe": chave_nfe,
        "numero_nf": numero_nf,
        "data_emissao": data_emissao,
        "fornecedor": {"cnpj": cnpj_fornecedor, "nome": nome_fornecedor, "uf": uf_fornecedor},
        "valor_total_nf": valor_total_nf,
        "itens": itens,
    }


def salvar_no_banco(dados):
    """Insere ou atualiza os dados no banco (Postgres ou SQLite)."""
    if not dados:
        return

    db.run(
        """
        INSERT INTO fornecedores (cnpj, nome, uf)
        VALUES (:cnpj, :nome, :uf)
        ON CONFLICT (cnpj) DO NOTHING
        """,
        {
            "cnpj": dados["fornecedor"]["cnpj"],
            "nome": dados["fornecedor"]["nome"],
            "uf": dados["fornecedor"]["uf"],
        },
    )

    db.run(
        """
        INSERT INTO notas_fiscais (chave_nfe, numero_nf, data_emissao, cnpj_fornecedor, valor_total)
        VALUES (:chave_nfe, :numero_nf, :data_emissao, :cnpj_fornecedor, :valor_total)
        ON CONFLICT (chave_nfe) DO UPDATE SET
            numero_nf       = excluded.numero_nf,
            data_emissao    = excluded.data_emissao,
            cnpj_fornecedor = excluded.cnpj_fornecedor,
            valor_total     = excluded.valor_total
        """,
        {
            "chave_nfe": dados["chave_nfe"],
            "numero_nf": dados["numero_nf"],
            "data_emissao": dados["data_emissao"],
            "cnpj_fornecedor": dados["fornecedor"]["cnpj"],
            "valor_total": dados["valor_total_nf"],
        },
    )

    db.run("DELETE FROM itens_nota WHERE chave_nfe = :chave_nfe", {"chave_nfe": dados["chave_nfe"]})

    db.run_many(
        """
        INSERT INTO itens_nota
            (chave_nfe, codigo_prod, descricao, ean, ncm, quantidade, valor_unitario, valor_total)
        VALUES
            (:chave_nfe, :codigo_prod, :descricao, :ean, :ncm, :quantidade, :valor_unitario, :valor_total)
        """,
        [
            {
                "chave_nfe": dados["chave_nfe"],
                "codigo_prod": item["codigo_prod"],
                "descricao": item["descricao"],
                "ean": item["ean"],
                "ncm": item["ncm"],
                "quantidade": item["quantidade"],
                "valor_unitario": item["valor_unitario"],
                "valor_total": item["valor_total"],
            }
            for item in dados["itens"]
        ],
    )


def importar_todos_xmls():
    """Percorre a pasta de XMLs e importa um por um. Retorna (sucessos, erros)."""
    inicializar_banco()

    if not PASTA_XMLS.exists():
        PASTA_XMLS.mkdir(parents=True, exist_ok=True)
        return 0, 0

    sucessos = 0
    erros = 0
    for arquivo in PASTA_XMLS.glob("*.xml"):
        try:
            dados = processar_xml(arquivo)
            if dados:
                salvar_no_banco(dados)
                sucessos += 1
        except Exception as e:
            erros += 1
            print(f"[VirtuAr] Erro em {arquivo.name}: {e}")

    return sucessos, erros


def salvar_xml_upload(arquivo):
    """Salva uma cópia do XML enviado."""
    PASTA_XMLS.mkdir(parents=True, exist_ok=True)
    nome_arquivo = Path(arquivo.name).name
    caminho_xml = PASTA_XMLS / nome_arquivo

    if not caminho_xml.exists():
        caminho_xml.write_bytes(arquivo.getvalue())

    return caminho_xml


if __name__ == "__main__":
    s, e = importar_todos_xmls()
    print(f"Importação concluída: {s} nota(s) com sucesso, {e} com erro.")
    print(f"Banco: {'Postgres' if db.IS_POSTGRES else db.engine.url}")