import discord
from discord.ext import commands, tasks
from discord import app_commands, ui
import datetime
import requests
import asyncio
import urllib.parse 
import json
import os
import io
import sqlite3
from quart import Quart, jsonify, request
from quart_cors import cors

# --- CONFIGURAÇÕES ---

TOKEN = 'MTQ5MDc5NDU0OTI1MTkzMjIyMA.GaYCxp.IHJq3ydAQAwhWRz0dj1YSczsSGp_waqYCaU030'
CHAVE_PIX = "a4d4b4ec-be58-4597-9d53-55937b7f0966"
NOME_PIX = "Bora Top Up"
CARTEIRA_USDT_BEP20 = "0x59cbc273d6f486bfdb4bb575b1d94238406e552b"
BINANCE_PAY_ID = "22920282" # ID da sua conta Binance para recebimento
CHAVE_GCASH = "09196933442 YO I L" 

# LISTA DE CARGOS QUE PODEM APROVAR PAGAMENTOS E GERIR TICKETS
IDS_CARGOS_ADM = [1493640026188283994] 

ID_CANAL_LOGS = 1490902565108584568
ID_CANAL_PAYMENT = 1494395959021666445
ID_CANAL_PRICES = 1490754684598616226
ID_CANAL_PHP = 1493341132153950288
ID_CANAL_FEEDBACK = 1493492226444099614 

# ID do canal onde o bot deve enviar os ficheiros HTML de Transcript
ID_CANAL_TRANSCRIPT_LOGS = 1497541178227167232 

# --- GERENCIADOR DE BANCO DE DADOS ---
class DatabaseManager:
    def __init__(self, db_path="bot_database.db"):
        self.db_path = db_path
        self.init_db()

    def connect(self):
        return sqlite3.connect(self.db_path)

    def init_db(self):
        with self.connect() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS service_config (
                    channel_id TEXT PRIMARY KEY,
                    user_id INTEGER,
                    language TEXT DEFAULT 'pt',
                    game TEXT DEFAULT 'default',
                    created_at TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS cart_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT,
                    item_slug TEXT,
                    item_name TEXT,
                    quantity INTEGER,
                    unit_price_usdt REAL,
                    FOREIGN KEY (channel_id) REFERENCES service_config(channel_id)
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sales_history (
                    order_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    user_name TEXT,
                    value REAL,
                    currency TEXT,
                    usdt_equivalent REAL,
                    coupon TEXT,
                    method TEXT,
                    timestamp DATETIME,
                    status TEXT DEFAULT 'completed'
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS bot_internal_data (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            ''')
            conn.commit()

    def save_service_config(self, channel_id, user_id, language='pt', game='default'):
        with self.connect() as conn:
            agora = datetime.datetime.now().isoformat()
            conn.execute('''
                INSERT OR REPLACE INTO service_config (channel_id, user_id, language, game, created_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (str(channel_id), user_id, language, game, agora))

    def get_service_config(self, channel_id):
        with self.connect() as conn:
            cursor = conn.execute('SELECT language, game, user_id FROM service_config WHERE channel_id = ?', (str(channel_id),))
            return cursor.fetchone()

    def add_to_cart(self, channel_id, slug, name, qty, price_usdt):
        with self.connect() as conn:
            cursor = conn.execute('SELECT id, quantity FROM cart_items WHERE channel_id = ? AND item_slug = ?', (str(channel_id), slug))
            row = cursor.fetchone()
            if row:
                conn.execute('UPDATE cart_items SET quantity = quantity + ? WHERE id = ?', (qty, row[0]))
            else:
                conn.execute('''
                    INSERT INTO cart_items (channel_id, item_slug, item_name, quantity, unit_price_usdt)
                    VALUES (?, ?, ?, ?, ?)
                ''', (str(channel_id), slug, name, qty, price_usdt))

    def get_cart(self, channel_id):
        with self.connect() as conn:
            cursor = conn.execute('SELECT item_slug, item_name, quantity, unit_price_usdt FROM cart_items WHERE channel_id = ?', (str(channel_id),))
            return cursor.fetchall()

    def clear_cart(self, channel_id):
        with self.connect() as conn:
            conn.execute('DELETE FROM cart_items WHERE channel_id = ?', (str(channel_id),))

    def record_sale(self, user_id, user_name, value, currency, usdt_val, coupon, method):
        with self.connect() as conn:
            agora = datetime.datetime.now().isoformat()
            conn.execute('''
                INSERT INTO sales_history (user_id, user_name, value, currency, usdt_equivalent, coupon, method, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, user_name, value, currency, usdt_val, coupon, method, agora))

    def get_all_history(self, limit=50):
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('SELECT * FROM sales_history ORDER BY timestamp DESC LIMIT ?', (limit,))
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def set_internal_data(self, key, value):
        with self.connect() as conn:
            conn.execute('INSERT OR REPLACE INTO bot_internal_data (key, value) VALUES (?, ?)', (key, str(value)))

    def get_internal_data(self, key, default=None):
        with self.connect() as conn:
            cursor = conn.execute('SELECT value FROM bot_internal_data WHERE key = ?', (key,))
            row = cursor.fetchone()
            return row[0] if row else default

db = DatabaseManager()

# --- SERVIDOR WEB (API PARA O DASHBOARD) ---
web_app = Quart(__name__)
web_app = cors(web_app, allow_origin="*")

@web_app.route('/api/stats', methods=['GET'])
async def get_stats():
    return jsonify({
        "vendas": ticket_stats["vendas_concluidas"],
        "brl": ticket_stats["valor_total_brl"],
        "usdt": ticket_stats["valor_total_usdt"],
        "php": ticket_stats["valor_total_php"],
        "uso_cupons": ticket_stats["uso_cupons"]
    })

@web_app.route('/api/history', methods=['GET'])
async def get_history():
    return jsonify(ticket_stats["historico"])

@web_app.route('/api/approve', methods=['POST'])
async def api_approve():
    data = await request.get_json()
    channel_id = data.get("channel_id")
    return jsonify({"status": "success", "message": f"Ticket {channel_id} processado"})

# --- NOVA ROTA DE ATUALIZAÇÃO (CORRIGIDA) ---
@web_app.route('/api/update_transaction', methods=['POST'])
async def update_transaction():
    try:
        data = await request.get_json()
        cliente = data.get("cliente")
        data_hora = data.get("originalDate")
        novo_valor = float(data.get("valor"))
        nova_moeda = data.get("moeda")

        encontrado = False
        for item in ticket_stats["historico"]:
            if item["cliente"] == cliente and item["data"] == data_hora:
                item["valor"] = novo_valor
                item["moeda"] = nova_moeda
                encontrado = True
                break
        
        if encontrado:
            ticket_stats["valor_total_brl"] = 0.0
            ticket_stats["valor_total_usdt"] = 0.0
            ticket_stats["valor_total_php"] = 0.0
            
            for item in ticket_stats["historico"]:
                val = float(item.get("valor", item.get("brl", item.get("usdt", item.get("php", 0)))))
                moe = item.get("moeda", "BRL")
                
                if moe == "BRL": ticket_stats["valor_total_brl"] += val
                elif moe == "USDT": ticket_stats["valor_total_usdt"] += val
                elif moe == "PHP": ticket_stats["valor_total_php"] += val
                
            # 3. Guardar as mudanças permanentemente
            save_data()
            return jsonify({"status": "success", "0": 200})
        
        return jsonify({"status": "error", "message": "Transação não encontrada"}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# --- PERSISTÊNCIA DE DADOS (AGORA USANDO SQLITE) ---
def save_data():
    try:
        db.set_internal_data("preferencia_idioma", json.dumps(preferencia_idioma))
        db.set_internal_data("preferencia_jogo", json.dumps(preferencia_jogo))
        db.set_internal_data("carrinhos", json.dumps(carrinhos))
        db.set_internal_data("ticket_stats", json.dumps(ticket_stats))
        db.set_internal_data("cotacao_php", bot.cotacao_php)
        db.set_internal_data("url_qr_php", bot.url_qr_php)
        db.set_internal_data("url_qr_binance", bot.url_qr_binance)
        db.set_internal_data("url_banner_gold", bot.url_banner_gold)
        db.set_internal_data("url_banner_wemix", bot.url_banner_wemix)
        db.set_internal_data("precos_manuais", json.dumps(PRECOS_MANUAIS))
    except Exception as e:
        print(f"⚠️ Erro ao guardar dados no DB: {e}")

def load_data():
    global preferencia_idioma, preferencia_jogo, carrinhos, ticket_stats, PRECOS_MANUAIS
    try:
        data_lang = db.get_internal_data("preferencia_idioma")
        if data_lang: preferencia_idioma.update(json.loads(data_lang))

        data_jogo = db.get_internal_data("preferencia_jogo")
        if data_jogo: preferencia_jogo.update(json.loads(data_jogo))

        data_car = db.get_internal_data("carrinhos")
        if data_car: carrinhos.update(json.loads(data_car))

        data_php = db.get_internal_data("cotacao_php")
        if data_php: bot.cotacao_php = float(data_php)

        data_qr_php = db.get_internal_data("url_qr_php")
        if data_qr_php: bot.url_qr_php = data_qr_php

        data_qr_bin = db.get_internal_data("url_qr_binance")
        if data_qr_bin: bot.url_qr_binance = data_qr_bin

        data_gold = db.get_internal_data("url_banner_gold")
        if data_gold: bot.url_banner_gold = data_gold

        data_wemix = db.get_internal_data("url_banner_wemix")
        if data_wemix: bot.url_banner_wemix = data_wemix

        data_precos = db.get_internal_data("precos_manuais")
        if data_precos: PRECOS_MANUAIS.update(json.loads(data_precos))

        data_stats = db.get_internal_data("ticket_stats")
        if data_stats: ticket_stats.update(json.loads(data_stats))
    except Exception as e:
        print(f"⚠️ Erro ao carregar dados do DB: {e}")

# --- ESTADO DO BOT ---
preferencia_idioma = {}
preferencia_jogo = {}
carrinhos = {}
PRECOS_MANUAIS = {
    "pt": {}, 
    "en": {}, 
    "ph": {}  
}
ticket_stats = {
    "vendas_concluidas": 0,
    "valor_total_brl": 0.0,
    "valor_total_usdt": 0.0,
    "valor_total_php": 0.0,
    "uso_cupons": {},
    "historico": []
}

CUPONS = {
    "VISHIBI": 0.025,
    "AOI": 0.025,
    "ANOA": 0.025,
    "MIRISHI": 0.025,
    "SONAY": 0.025,
}

URL_BANNER_LOJA = "https://cdn.discordapp.com/attachments/1242284776275185826/1497495672864112671/content.png?ex=69edbb06&is=69ec6986&hm=5211ac4892f347ce9bed36058b747a5bc86ae1b3e2d14147649b6ed5aee9bcb9&"
URL_BANNER_SELECAO = "https://media.discordapp.net/attachments/1492912195049095208/1493473548545691791/content.png"
URL_BANNER_ESCOLHA = "https://media.discordapp.net/attachments/1486992454610845708/1492228732981743716/content.png"

IMAGENS_JOGOS = {
    "mir4": URL_BANNER_SELECAO,
    "default": URL_BANNER_ESCOLHA
}

LANGUAGES = {
    "pt": {
        "escolha_jogo_titulo": "🛒 Selecione um jogo para começar",
        "escolha_jogo_desc": "✨ Use o menu abaixo para escolher o jogo que deseja comprar.\n\n📄 Após selecionar o jogo, adicione os **pacotes ao carrinho**.",
        "placeholder_jogo": "Selecione um jogo...",
        "loja_titulo": "✨ Loja {0}",
        "loja_desc": "Compre Gold ou Cash para {0}!",
        "cotacao_label": "Cotação",
        "resumo_titulo": "🛒 RESUMO DO SEU PEDIDO",
        "qtd_label": "Qtd",
        "total_brl_label": "Total em BRL",
        "total_usdt_label": "Total em USDT",
        "subtotal_label": "Subtotal",
        "desconto_label": "Desconto ({perc}%)",
        "resumo_cupom_titulo": "🏷️ **CUPOM APLICADO:**",
        "cotacao_footer": "Cotação Binance: R$ {val:.2f}",
        "fechar_canal": "Cancelar / Fechar",
        "encerrando": "A encerrar atendimento em 3 segundos...",
        "finalizar_btn": "Finalizar Compra",
        "confirmar_pag_btn": "Confirmar Pagamento",
        "add_mais_btn": "Adicionar Mais",
        "remover_btn": "Remover Item",
        "pagamento_titulo": "🏁 Pagamento",
        "metodo_desc": "Escolha o método de pagamento abaixo:",
        "suporte_msg": "Olá {user}, explique a sua dúvida detalhadamente. <@&{adm}>",
        "suporte_footer": "Apenas a administração pode encerrar este ticket.",
        "prompt_lang_title": "🌐 Seleção de Idioma",
        "prompt_lang_desc": "Selecione o idioma para este atendimento:",
        "fechar_ticket_btn": "Concluir Ticket",
        "erro_perm": "❌ Apenas a Administração pode realizar esta ação.",
        "cancelar_compra_btn": "Cancelar Compra",
        "placeholder_item": "Selecione o item...",
        "modal_qtd_titulo": "Quantidade",
        "modal_qtd_label": "Quantos pacotes deseja?",
        "pix_titulo": "💠 Pagamento via PIX",
        "pix_desc": "Ao realizar o pagamento envie um print do comprovante do Pix, clique em Já paguei e aguarde que um administrador confirme seu pagamento para dar sequencia ao processo.\n\n**Chave PIX**\n`{key}`\n\n**Valores**\n`R$ {val:.2f}` ou `{usdt:.2f} USDT`",
        "val_invalido": "❌ Valor inválido. Insira apenas números.",
        "alerta_staff": "🔔 Novo atendimento de **compra** iniciado por {user}. <@&{adm}>",
        "aguardando_staff": "⏳ **Pagamento enviado para análise!** Aguarde um administrador confirmar.",
        "pagamento_aprovado": "✅ **Pagamento Aprovado!**",
        "pagamento_recusado": "❌ **Pagamento Recusado!** Verifique o comprovante ou fale com o suporte.",
        "form_entrega_titulo": "📝 Finalizar Entrega",
        "form_entrega_desc": "Seu pagamento foi aprovado e um novo ticket de entrega foi criado!\n\nPara que possamos realizar a sua recarga, precisamos que você forneça os dados de acesso (login) da sua conta através do botão abaixo.\n\n{itens}\n*Clique no botão abaixo para preencher os dados:*",
        "btn_preencher": "Preencher Dados",
        "modal_titulo_dados": "Dados para Recarga",
        "modal_campo_login": "Método de Login (Google/FB/etc)",
        "modal_campo_email": "E-mail de Acesso",
        "modal_campo_senha": "Senha",
        "modal_campo_char": "Nome do Personagem",
        "modal_campo_servidor": "Server / Região",
        "modal_placeholder_login": "Ex: Google / Facebook / Apple",
        "modal_placeholder_char": "Nome do seu personagem",
        "modal_placeholder_servidor": "Ex: SA-11 / NA-31",
        "dados_rece_bidos": "✅ **Dados recebidos com sucesso!** Nossa equipe iniciará a recarga em breve.",
        "embed_dados_titulo": "🔑 DADOS DE ACESSO RECEBIDOS",
        "embed_dados_cliente": "👤 Cliente",
        "embed_dados_metodo": "🕹️ Método",
        "embed_dados_email": "E-mail",
        "embed_dados_senha": "Senha",
        "embed_dados_char": "🐉 Personagem",
        "embed_dados_servidor": "🌐 Server",
        "alerta_dados_preenchidos": "🔔 <@&{adm}> os dados foram preenchidos.",
        "cupom_btn": "Cupom de Desconto",
        "cupom_modal_titulo": "Aplicar Cupom",
        "cupom_modal_label": "Insira o código do cupom",
        "cupom_invalido": "❌ Cupom inválido ou expirado.",
        "cupom_aplicado": "✅ Cupom `{code}` aplicado!",
        "voltar_btn": "Voltar",
        "placeholder_remover": "Selecione o item para remover...",
        "binance_titulo": "🔶 Pagamento via Binance (USDT)",
        "binance_desc": "Ao realizar o pagamento envie um print do comprovante da Binance, clique em Já paguei e aguarde que um administrador confirme seu pagamento para dar sequencia ao processo.\n\n**Wallet (BEP20)**\n`{key}`\n\n**Valores**\n`{val:.2f} USDT`",
        "resumo_compra_staff": "📦 ITENS COMPRADOS",
        "staff_aprovado_msg": "✅ Pagamento aprovado! Novo tópico:",
        "staff_recusado_msg": "❌ Recusado por",
        "metodo_selecionado": "{user}, metodo selecionado: **{method}**.",
        "status_analise_titulo": "💸 Pagamento em análise",
        "venda_ouro_msg": "Olá {user}, você abriu um ticket para **Venda de Ouro**. Por favor, aguarde um administrador. <@&{adm}>",
        "venda_wemix_msg": "Olá {user}, você abriu um ticket para **Venda de Wemix**. Por favor, aguarde um administrador. <@&{adm}>",
        "aviso_adm_pagamento": "⚠️ Apenas um administrador pode validar e aprovar o seu pagamento.",
        "feedback_dm_title": "🎉 Pedido Concluído!",
        "feedback_dm_desc": "Seu pedido foi finalizado com sucesso pela equipe BORA TOP UP!\n\nGostaríamos muito de saber como foi sua experiênca. Poderia nos deixar um feedback rápido?",
        "feedback_btn_label": "Enviar Feedback",
        "feedback_modal_title": "Feedback - BORA TOP UP",
        "feedback_modal_nota": "Sua Nota (0 a 10)",
        "feedback_modal_comment": "Seu Comentário",
        "feedback_modal_placeholder_nota": "Ex: 10",
        "feedback_modal_placeholder_comment": "Conte-nos como foi sua experiência...",
        "feedback_success": "✅ Muito obrigado pelo seu feedback! Isso nos ajuda a crescer.",
        "item_gold_nome": "🥇 Gold {val:,}"
    },
    "en": {
        "escolha_jogo_titulo": "🛒 Order Center",
        "escolha_jogo_desc": "✨ Select a game from the menu below.\n\n📄 Then add packs to your cart and finish your purchase.",
        "placeholder_jogo": "Choose your game...",
        "loja_titulo": "✨ {0} Store",
        "loja_desc": "Buy Gold or Cash for {0}!",
        "cotacao_label": "Rate",
        "resumo_titulo": "🛒 ORDER SUMMARY",
        "qtd_label": "Qty",
        "total_brl_label": "Total in BRL",
        "total_usdt_label": "Total in USDT",
        "subtotal_label": "Subtotal",
        "desconto_label": "Discount ({perc}%)",
        "resumo_cupom_titulo": "🏷️ **COUPON APPLIED:**",
        "cotacao_footer": "Binance Rate: R$ {val:.2f}",
        "fechar_canal": "CANCEL / CLOSE",
        "encerrando": "Closing ticket in 3 seconds...",
        "finalizar_btn": "FINISH PURCHASE",
        "confirmar_pag_btn": "Confirm Payment",
        "add_mais_btn": "ADD MORE",
        "remover_btn": "REMOVE ITEM",
        "pagamento_titulo": "🏁 Payment",
        "metodo_desc": "Choose your payment method below:",
        "suporte_msg": "Hello {user}, please explain your issue in detail. <@&{adm}>",
        "suporte_footer": "Only administration can close this ticket.",
        "item_gold_nome": "🥇 Gold {val:,}",
        "item_pack_nome": "🎁 {label}",
        "comprar_gold_titulo": "💰 Buy Gold",
        "comprar_gold_label": "Gold amount (1000 - 9999)",
        "prompt_lang_title": "🌐 Language Selection",
        "prompt_lang_desc": "Please select the language for this ticket:",
        "fechar_ticket_btn": "Complete Ticket",
        "erro_perm": "❌ Only Administration can perform this action.",
        "cancelar_compra_btn": "Cancel Purchase",
        "placeholder_item": "Select item...",
        "modal_qtd_titulo": "Quantity",
        "modal_qtd_label": "How many packs do you want?",
        "pix_titulo": "Manual Binance Payment",
        "pix_desc": "Send the total amount to the wallet below:\n\n🔶 **USDT (BEP20):** `{key}`\n💰 **Total Value:** `{val:.2f} USDT`\n\n*Current Rate: 1 USDT = R$ {rate:.2f}*\n\n*After paying, click the confirm button.*",
        "val_invalido": "❌ Invalid value. Please enter numbers only.",
        "alerta_staff": "🔔 New **purchase** ticket started by {user}. <@&{adm}>",
        "aguardando_staff": "⏳ **Payment sent for analysis!** Please wait for an admin to confirm.",
        "pagamento_aprovado": "✅ **Payment Approved!**",
        "pagamento_recusado": "❌ **Payment Rejected** Please double check the payment details or contact support",
        "form_entrega_titulo": "📝 Finalize Delivery",
        "form_entrega_desc": "Your payment has been approved and a new delivery ticket has been created!\n\nIn order for us to complete your top-up, we need you to provide the access data (login) for your account using the button below.\n\n{itens}\n*Click the button below to fill in the details:*",
        "btn_preencher": "Fill Information",
        "modal_titulo_dados": "Top-up Details",
        "modal_campo_login": "Login Method (Google/FB/etc)",
        "modal_campo_email": "Access E-mail",
        "modal_campo_senha": "Password",
        "modal_campo_char": "Character Name",
        "modal_campo_servidor": "Server / Region",
        "modal_placeholder_login": "Ex: Google / Facebook / Apple",
        "modal_placeholder_char": "Your character name",
        "modal_placeholder_servidor": "Ex: SA-11 / NA-31",
        "dados_rece_bidos": "✅ **Data received!** Our team will start the top-up shortly.",
        "embed_dados_titulo": "🔑 ACCESS DATA RECEIVED",
        "embed_dados_cliente": "👤 Client",
        "embed_dados_metodo": "🕹️ Method",
        "embed_dados_email": "📧 E-mail",
        "embed_dados_senha": "🔐 Password",
        "embed_dados_char": "🐉 Character",
        "embed_dados_servidor": "🌐 Server",
        "alerta_dados_preenchidos": "🔔 <@&{adm}> data has been filled.",
        "cupom_btn": "Discount Coupon",
        "cupom_modal_titulo": "Apply Coupon",
        "cupom_modal_label": "Enter coupon code",
        "cupom_invalido": "❌ Invalid or expired coupon.",
        "cupom_aplicado": "✅ Coupon `{code}` applied!",
        "voltar_btn": "Back",
        "placeholder_remover": "Select item to remove...",
        "binance_titulo": "🔶 Binance Payment (USDT)",
        "binance_desc": "When making payment, send a print of the Binance receipt, click I've paid and wait for an administrator to confirm your payment.\n\n**Wallet (BEP20)**\n`{key}`\n\n**Values**\n`{val:.2f} USDT`",
        "resumo_compra_staff": "📦 PURCHASED ITEMS",
        "staff_aprovado_msg": "✅ Payment approved! New chat will appear:",
        "staff_recusado_msg": "❌ Rejected by",
        "metodo_selecionado": "{user}, selected method: **{method}**.",
        "status_analise_titulo": "💸 Payment under analysis",
        "venda_ouro_msg": "Hello {user}, you opened a ticket for **Gold Sell**. Please wait for an administrator. <@&{adm}>",
        "venda_wemix_msg": "Hello {user}, you opened a ticket for **Wemix Sell**. Please wait for an administrator. <@&{adm}>",
        "aviso_adm_pagamento": "⚠️ Only an administrator can validate and approve your payment.",
        "feedback_dm_title": "🎉 Order Completed!",
        "feedback_dm_desc": "Your order has been successfully completed by the BORA TOP UP team!\n\nWe would love to know how your experience was. Could you leave us a quick feedback?",
        "feedback_btn_label": "Send Feedback",
        "feedback_modal_title": "Feedback - BORA TOP UP",
        "feedback_modal_nota": "Your Rating (0 to 10)",
        "feedback_modal_comment": "Your Comment",
        "feedback_modal_placeholder_nota": "Ex: 10",
        "feedback_modal_placeholder_comment": "Tell us about your experience...",
        "feedback_success": "✅ Thank you very much for your feedback! It helps us grow.",
        "resumo_cupom_titulo": "🏷️ **COUPON APPLIED:**"
    },
    "ph": {
        "escolha_jogo_titulo": "🛒 Buy",
        "escolha_jogo_desc": "✨ Pumili ng laro sa ibaba.\n\n📄 Pagkatapos ay magdagdag ng mga pack sa iyong cart at tapusin ang pag bupili.",
        "placeholder_jogo": "Pumili ng iyong laro...",
        "loja_titulo": "✨ {0} Store",
        "loja_desc": "Bumili ng Gold o Cash para sa {0}!",
        "cotacao_label": "Rate",
        "resumo_titulo": "🛒 Order Summary",
        "qtd_label": "Dami",
        "total_brl_label": "Total PHP",
        "total_usdt_label": "Kabuuan sa PHP",
        "cotacao_footer": "Binance Rate: R$ {val:.2f}",
        "fechar_canal": "Kanselahin / Isara",
        "encerrando": "Isinasara ang ticket sa loob OF 3 segundo...",
        "finalizar_btn": "Tapusin Ang Pagbili",
        "confirmar_pag_btn": "Kumpirmahin ang Bayad",
        "add_mais_btn": "Magdagdag Pa",
        "remover_btn": "Alisin Ang Item",
        "pagamento_titulo": "🏁 Pagbabayad",
        "metodo_desc": "Pumili ng paraan ng pagbabayad sa ibaba:",
        "suporte_msg": "Kumusta {user}, pakipaliwanag ang iyong isyu nang detalyado. <@&{adm}>",
        "suporte_footer": "Tanging administrasyon lamang ang maaaring magsara ng ticket na ito.",
        "prompt_lang_title": "🌐 Pagpili ng Wika",
        "prompt_lang_desc": "Mangyaring piliin ang wika para sa ticket na ito:",
        "fechar_ticket_btn": "Conclude Ticket",
        "erro_perm": "❌ Tanging Administrasyon lamang ang maaaring magsagawa nito.",
        "cancelar_compra_btn": "Kanselahin ang Pagbili",
        "placeholder_item": "Pumili ng item...",
        "modal_qtd_titulo": "Dami",
        "modal_qtd_label": "Ilang pack ang gusto mo?",
        "pix_titulo": "Manual Payment Info",
        "pix_desc": "Ipadala ang kabuuang halaga sa wallet o account sa ibaba:\n\n🔶 **USDT (BEP20):** `{key}`\n💰 **Kabuuang Halaga:** `{val:.2f} PHP`\n\n*Current Rate: 1 USDT = {rate:.2f} PHP*\n\n*Pagkatapos magbayad, i-click ang confirm button.*",
        "gcash_titulo": "💠 GCash Payment Info",
        "gcash_desc": "Kapag nagbabayad, magpadala ng screenshot ng resibo ng GCash, i-click ang Já paguei at hintayin ang kumpirmasyon ng admin.\n\n**GCash Number**\n`{key}`\n\n**Valores**\n`{val:.2f} PHP`",
        "val_invalido": "❌ Maling halaga. Mangyaring magpasok ng mga numero lamang.",
        "alerta_staff": "🔔 Bagong **purchase** ticket na sinimulan ni {user}. <@&{adm}>",
        "aguardando_staff": "⏳ **Ang bayad ay naipadala na para sa pagsusuri!** Mangyaring maghintay para sa kumpirmasyon ng admin.",
        "pagamento_aprovado": "✅ **Payment Approved!** Inihahanda na ang iyong order...",
        "pagamento_recusado": "❌ **Payment Rejected.** Paki double check ang payment details o makipag ugnayan sa support",
        "form_entrega_titulo": "📝 Tapusin ang Paghahatid",
        "form_entrega_desc": "Ang iyong bayad ay naaprubahan na at isang bagong delivery ticket ang nagawa!\n\nUpang makumpleto namin ang iyong top-up, kailangan namim ibigay mo ang data sa pag-access (login) para sa iyong account gamit ang button sa ibaba.\n\n{itens}\n*I-click ang button sa ibaba upang punan ang mga detalye:*",
        "btn_preencher": "Ibigay ang Impormasyon",
        "modal_titulo_dados": "Mga Detalye ng Top-up",
        "modal_campo_login": "Paraan ng Pag-login (Google/FB/etc)",
        "modal_campo_email": "Access E-mail",
        "modal_campo_senha": "Password",
        "modal_campo_char": "Pangalan ng Character",
        "modal_campo_servidor": "Server / Rehiyon",
        "modal_placeholder_login": "Paraan (Google/FB/etc)",
        "modal_placeholder_char": "Pangalan ng Character",
        "modal_placeholder_servidor": "Server (ex: SA-11)",
        "dados_rece_bidos": "✅ **Natanggap na ang data!** Magsisimula ang aming team sa top-up sa lalong madaling panahon.",
        "embed_dados_titulo": "🔑 NATANGGAP NA ANG ACCESS DATA",
        "embed_dados_cliente": "👤 Client",
        "embed_dados_metodo": "🕹️ Paraan",
        "embed_dados_email": "📧 E-mail",
        "embed_dados_senha": "🔐 Password",
        "embed_dados_char": "🐉 Character",
        "embed_dados_servidor": "🌐 Server",
        "alerta_dados_preenchidos": "🔔 <@&{adm}> naibigay na ang data.",
        "cupom_btn": "Discount Coupon",
        "cupom_modal_titulo": "Ilapat ang Coupon",
        "cupom_modal_label": "Ilagay ang code ng coupon",
        "cupom_invalido": "❌ Invalid o expired na coupon.",
        "cupom_aplicado": "✅ Coupon `{code}` ay nailapat na!",
        "desconto_label": "Discount ({perc}%)",
        "subtotal_label": "Subtotal",
        "voltar_btn": "Bumalik",
        "placeholder_remover": "Pumili ng item na aalisin...",
        "binance_titulo": "🔶 Binance Payment (USDT)",
        "binance_desc": "Kapag nagbabayad, magpadala ng screenshot ng resibo ng Binance, i-click ang Já paguei at hintayin ang kumpirmasyon ng admin.\n\n**Wallet (BEP20)**\n`{key}`\n\n**Valores**\n`{val:.2f} USDT`",
        "resumo_compra_staff": "📦 BINILI NA MGA ITEM",
        "staff_aprovado_msg": "✅ Aprubado na ang Bayad! Bagong thread:",
        "staff_recusado_msg": "❌ Tinanggihan ni",
        "metodo_selecionado": "{user}, napiling paraan: **{method}**.",
        "status_analise_titulo": "💸 Pagbabayad sa ilalim ng pagsusuri",
        "venda_ouro_msg": "Kumusta {user}, nagbukas ka ng ticket para sa **Gold Sell**. Mangyaring maghintay para sa umaasawang administrator. <@&{adm}>",
        "venda_wemix_msg": "Kumusta {user}, nagbukas ka ng ticket para sa **Wemix Sell**. Mangyaring maghintay para sa umaasawang administrator. <@&{adm}>",
        "aviso_adm_pagamento": "⚠️ Isang administrador lamang ang maaaring mag-validate at mag-apruba ng iyong bayad.",
        "feedback_dm_title": "🎉 Nakumpleto ang Order!",
        "feedback_dm_desc": "Matagumpay na natapos ang iyong order ng BORA TOP UP team!\n\nGusto naming malaman ang iyong karanasan. Maaari ka bang mag-iwan ng mabilis na feedback?",
        "feedback_btn_label": "Magpadala ng Feedback",
        "feedback_modal_title": "Feedback - BORA TOP UP",
        "feedback_modal_nota": "Iyong Rating (0 hanggang 10)",
        "feedback_modal_comment": "Iyong Komento",
        "feedback_modal_placeholder_nota": "Ex: 10",
        "feedback_modal_placeholder_comment": "Sabihin sa amin ang iyong karanasan...",
        "feedback_success": "✅ Maraming salamat sa iyong feedback! Nakakatulong ito sa aming pag laro.",
        "resumo_cupom_titulo": "🏷️ **CUPON ILAPAT:**"
    }
}

async def generate_transcript(channel):
    """Gera um ficheiro HTML estilizado com o histórico do canal (Discord Theme)."""
    messages_html = ""
    
    # Lista de títulos de Embeds que contêm dados sensíveis e devem ser ignorados no Transcript
    titles_to_skip = [
        "🔑 DADOS DE ACESSO RECEBIDOS",
        "🔑 ACCESS DATA RECEIVED",
        "🔑 NATANGGAP NA ANG ACCESS DATA"
    ]
    
    async for message in channel.history(limit=None, oldest_first=True):
        # Verifica se a mensagem contém um Embed com um dos títulos proibidos
        skip_message = False
        if message.embeds:
            for embed in message.embeds:
                if embed.title in titles_to_skip:
                    skip_message = True
                    break
        
        if skip_message:
            continue
            
        time = message.created_at.strftime("%d/%m/%Y %H:%M")
        author_name = message.author.display_name
        author_avatar = message.author.display_avatar.url if message.author.display_avatar else "https://cdn.discordapp.com/embed/avatars/0.png"
        content = message.content.replace('\n', '<br>') if message.content else ""
        
        messages_html += f"""
        <div class="message">
            <img class="avatar" src="{author_avatar}" alt="avatar">
            <div class="content">
                <div class="header">
                    <span class="author">{author_name}</span>
                    <span class="timestamp">{time}</span>
                </div>
                <div class="text">{content}</div>
        """
        
        if message.attachments:
            for att in message.attachments:
                if any(att.filename.lower().endswith(ext) for ext in ['png', 'jpg', 'jpeg', 'gif', 'webp']):
                    messages_html += f'<div class="attachment"><img src="{att.url}" style="max-width: 400px; border-radius: 4px; margin-top: 8px;"></div>'
                else:
                    messages_html += f'<div class="attachment"><a href="{att.url}" target="_blank" style="color: #00aff4;">Ficheiro: {att.filename}</a></div>'
        
        # Se houver embeds (que não foram filtrados no início), renderiza apenas o título/descrição de forma simples
        if message.embeds:
            for embed in message.embeds:
                messages_html += f'<div style="border-left: 4px solid #4f545c; padding-left: 10px; margin-top: 5px;">'
                if embed.title: messages_html += f'<strong>{embed.title}</strong><br>'
                if embed.description: messages_html += f'<span>{embed.description}</span>'
                messages_html += '</div>'
        
        messages_html += "</div></div>"

    html_full = f"""
    <!DOCTYPE html>
    <html lang="pt">
    <head>
        <meta charset="utf-8">
        <title>Transcript - {channel.name}</title>
        <style>
            body {{ background-color: #36393f; color: #dcddde; font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; padding: 20px; }}
            .container {{ max-width: 900px; margin: 0 auto; }}
            .message {{ display: flex; margin-bottom: 20px; padding: 5px; border-radius: 5px; transition: background-color 0.2s; }}
            .message:hover {{ background-color: #32353b; }}
            .avatar {{ width: 40px; height: 40px; border-radius: 50%; margin-right: 16px; flex-shrink: 0; }}
            .content {{ flex: 1; min-width: 0; }}
            .header {{ margin-bottom: 4px; }}
            .author {{ color: #ffffff; font-weight: 500; font-size: 1rem; margin-right: 8px; }}
            .timestamp {{ color: #72767d; font-size: 0.75rem; }}
            .text {{ font-size: 1rem; line-height: 1.375rem; white-space: pre-wrap; word-wrap: break-word; }}
            .divider {{ border-bottom: 1px solid #42454a; margin: 20px 0; }}
            h1 {{ color: white; text-align: center; margin-bottom: 0; }}
            .sub-info {{ color: #8e9297; text-align: center; font-size: 0.9rem; margin-top: 5px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Relatório de Atendimento</h1>
            <div class="sub-info">Canal: {channel.name} | ID: {channel.id}</div>
            <div class="divider"></div>
            {messages_html}
        </div>
    </body>
    </html>
    """
    return html_full

async def update_sales_stats(user_name, brl_val, usdt_val, php_val, coupon_code=None):
    """Atualiza as estatísticas globais e o histórico de vendas."""
    ticket_stats["vendas_concluidas"] += 1
    ticket_stats["valor_total_brl"] += brl_val
    ticket_stats["valor_total_usdt"] += usdt_val
    ticket_stats["valor_total_php"] += php_val
    
    if coupon_code and coupon_code != "Nenhum":
        code = coupon_code.upper()
        ticket_stats["uso_cupons"][code] = ticket_stats["uso_cupons"].get(code, 0) + 1
        
    agora_str = datetime.datetime.now().strftime("%d/%m %H:%M")
    
    # Integração com a tabela de histórico de vendas do Banco de Dados
    db.record_sale(0, user_name, brl_val, "BRL", usdt_val, coupon_code or "Nenhum", "Discord App")
    
    ticket_stats["historico"].insert(0, {
        "cliente": user_name, 
        "brl": brl_val, 
        "usdt": usdt_val, 
        "php": php_val,
        "data": agora_str,
        "cupom": coupon_code or "Nenhum"
    })
    
    if len(ticket_stats["historico"]) > 50:
        ticket_stats["historico"].pop()
    save_data()

precos_base_usd = {
    "pack_1": 0.90, 
    "pack_3": 2.50, 
    "pack_5": 4.00, 
    "pack_10": 7.50, 
    "pack_30": 22.50, 
    "pack_50": 40.00, 
    "pack_100": 74.00,
    "gold_1k_usd": 4.20
}

intents = discord.Intents.all()

class MirishiBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.cotacao_dolar = 5.20 
        self.cotacao_php = 56.50 
        self.url_qr_php = "" 
        self.url_qr_binance = "" 
        self.url_banner_gold = "https://media.discordapp.net/attachments/1486992454610845708/1492228732981743716/content.png"
        self.url_banner_wemix = "https://media.discordapp.net/attachments/1486992454610845708/1492228732981743716/content.png"

    async def setup_hook(self):
        load_data() 
        self.atualizar_cotacao_task.start()
        self.verificar_inatividade_task.start() # REATIVADO para recarregar o cache das views ativas de forma rotativa!
        # Inicia o servidor Web em segundo plano
        self.loop.create_task(web_app.run_task(host="0.0.0.0", port=5000))
        self.add_view(LojaMenu())
        self.add_view(LojaMenuPH())
        self.add_view(TabelaPrecosView()) # Registro da view persistente da tabela de preços
        await self.tree.sync()
        print("✅ Bora Top UP / API Web Online")

    @tasks.loop(hours=2) 
    async def atualizar_cotacao_task(self):
        try:
            res_brl = requests.get("https://api.binance.com/api/v3/ticker_price?symbol=USDTBRL", timeout=10).json()
            self.cotacao_dolar = float(res_brl['price'])
            # Atualiza automaticamente a tabela de preços ativa se houver uma cadastrada
            await atualizar_mensagem_precos()
        except Exception:
            pass

    @tasks.loop(minutes=5)
    async def verificar_inatividade_task(self):
        """Verifica todas as threads ativas do bot a cada 5 minutos e atualiza os botões (cache)."""
        try:
            for guild in self.guilds:
                for thread in guild.threads:
                    if thread.name.startswith(("🛒-", "💰-", "💎-", "💬-", "🇧🇷-", "🇺🇸-", "🇵🇭-")) and not thread.archived:
                        await refresh_ticket_view(thread.id)
        except Exception as e:
            print(f"⚠️ Erro ao atualizar views automáticas dos tickets: {e}")

bot = MirishiBot()

# --- UTILITÁRIOS ---

def get_img(jogo):
    return IMAGENS_JOGOS.get(jogo.lower(), IMAGENS_JOGOS["default"])

def get_text(channel_id, key):
    c_id = str(channel_id)
    lang = preferencia_idioma.get(c_id, "pt")
    return LANGUAGES[lang].get(key, LANGUAGES["pt"].get(key, f"Key {key} missing"))

def has_admin_permission(user):
    return any(role.id in IDS_CARGOS_ADM for role in user.roles)

async def refresh_ticket_view(channel_id):
    """Atualiza silenciosamente a view ativa no ticket para restaurar o cache do Discord."""
    try:
        state_data = db.get_internal_data(f"view_state_{channel_id}")
        if not state_data:
            return

        config = db.get_service_config(channel_id)
        db_user_id = int(config[2]) if config else None

        state = json.loads(state_data)
        msg_id = state.get("message_id")
        view_type = state.get("type")
        user_id = state.get("user_id") or db_user_id

        channel = bot.get_channel(int(channel_id))
        if not channel:
            try:
                channel = await bot.fetch_channel(int(channel_id))
            except:
                return

        message = None
        if msg_id:
            try:
                message = await channel.fetch_message(int(msg_id))
            except discord.NotFound:
                pass

        if not message:
            async for msg in channel.history(limit=15):
                if msg.author == bot.user and msg.components:
                    message = msg
                    break

        if not message:
            return

        # Re-inicializa a view baseado no último estado persistido
        view = None
        if view_type == "EscolhaJogo":
            view = EscolhaJogo(channel_id)
        elif view_type == "SelecaoItens":
            view = SelecaoItens(channel_id)
        elif view_type == "CarrinhoAcoes":
            view = CarrinhoAcoes(channel_id)
        elif view_type == "StaffManualConcludeView":
            view = StaffManualConcludeView(channel_id, user_id)
        elif view_type == "AprovadoFormView":
            view = AprovadoFormView(channel_id)
        elif view_type == "UserConfirmPaymentView":
            view = UserConfirmPaymentView(
                user_id=user_id,
                channel_id=channel_id,
                final_usdt=state.get("final_usdt", 0.0),
                method_name=state.get("method_name", "PIX"),
                instr_pag=state.get("instr_pag", "")
            )
        elif view_type == "StatusAnaliseUserView":
            view = StatusAnaliseUserView(user_id, channel_id)

        if view:
            await message.edit(view=view)
            if state.get("message_id") != message.id:
                state["message_id"] = message.id
                db.set_internal_data(f"view_state_{channel_id}", json.dumps(state))
    except Exception:
        pass # Ignora erros de tópicos indisponíveis

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    # Atualização inicial de todos os tickets em execução quando o bot liga
    try:
        count = 0
        for guild in bot.guilds:
            for thread in guild.threads:
                if thread.name.startswith(("🛒-", "💰-", "💎-", "💬-", "🇧🇷-", "🇺🇸-", "🇵🇭-")) and not thread.archived:
                    await refresh_ticket_view(thread.id)
                    count += 1
        if count > 0:
            print(f"🔄 Re-sincronizados {count} tickets ativos ao iniciar.")
    except Exception as e:
        print(f"⚠️ Erro no warmup inicial de tickets: {e}")

# --- GERADOR DINÂMICO DE TABELA DE PREÇOS (DESIGN EXCLUSIVO E REESTILIZADO) ---
# Alinhado com a image_6c6206_2.png, mas com layout alternativo e de alta legibilidade para não parecer cópia direta
def gerar_embed_tabela_precos():
    embed = discord.Embed(
        title="⚡ TABELA DE PREÇOS OFICIAL — BORA TOP UP ⚡",
        color=0x1abc9c, # Tom moderno ciano/turquesa
        timestamp=datetime.datetime.now()
    )
    
    desc = "✨ *Selecione os pacotes desejados e abra um ticket de atendimento para finalizar com facilidade.*\n"
    desc += "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    packs = [
        ("Pacote $1 🎁", 0.90, "pack_1"),
        ("Pacote $3 🎁", 2.65, "pack_3"),
        ("Pacote $5 🎁", 3.78, "pack_5"),
        ("Pacote $10 🎁", 7.56, "pack_10"),
        ("Pacote $30 🎁", 22.68, "pack_30"),
        ("Passe $40 🎁", 30.24, "pack_40"),
        ("Pacote $50 🎁", 40.00, "pack_50"),
        ("Pacote $100 🎁", 75.50, "pack_100")
    ]
    
    for label, usd_val, slug in packs:
        # Busca o valor manual em BRL, caso não haja faz conversão base
        p_manual_pt = PRECOS_MANUAIS["pt"].get(slug)
        brl_val = p_manual_pt if p_manual_pt is not None else (usd_val * bot.cotacao_dolar)
        
        # Busca o valor manual em USD, caso não haja mantém valor base
        p_manual_en = PRECOS_MANUAIS["en"].get(slug)
        usd_val_final = p_manual_en if p_manual_en is not None else usd_val
        
        desc += f"⭐ **{label}**\n"
        desc += f"⤷ `USD $ {usd_val_final:.2f}` ➔ **`R$ {brl_val:.2f}`**\n\n"
        
    desc += "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    desc += "🎫 **Precisa comprar? Clique no botão verde abaixo para abrir seu atendimento!**\n"
    agora_str = datetime.datetime.now().strftime("%d/%m/%Y às %H:%M")
    desc += f"🔄 *Cotação Base: R$ {bot.cotacao_dolar:.2f} | Atualizado em: {agora_str}*"
    
    embed.description = desc
    embed.set_footer(text="Bora Top Up • Compromisso com a rapidez e segurança")
    return embed

async def atualizar_mensagem_precos():
    """Tenta localizar e atualizar a mensagem da tabela de preços dinâmica ativa no Discord."""
    try:
        channel_id_str = db.get_internal_data("price_message_channel_id")
        msg_id_str = db.get_internal_data("price_message_id")
        if channel_id_str and msg_id_str:
            channel_id = int(channel_id_str)
            msg_id = int(msg_id_str)
            
            channel = bot.get_channel(channel_id)
            if not channel:
                try:
                    channel = await bot.fetch_channel(channel_id)
                except:
                    return
            
            if channel:
                try:
                    msg = await channel.fetch_message(msg_id)
                    if msg:
                        embed = gerar_embed_tabela_precos()
                        # Preserva a view da tabela de preços
                        await msg.edit(embed=embed, view=TabelaPrecosView())
                except discord.NotFound:
                    pass
    except Exception as e:
        print(f"⚠️ Erro ao atualizar dinamicamente a tabela de preços: {e}")

# --- CLASSES INTERATIVAS PARA O PAINEL DE PREÇOS EM TEMPO REAL ---

class AlteraPrecoModal(ui.Modal):
    def __init__(self, currency, pack_slug):
        super().__init__(title=f"Alterar Preço do {pack_slug.upper()}")
        self.currency = currency
        self.pack_slug = pack_slug
        
        self.val_input = ui.TextInput(
            label=f"Novo valor em {self.currency.upper()}",
            placeholder="Ex: 5.50",
            required=True,
            max_length=10
        )
        self.add_item(self.val_input)
        
    async def on_submit(self, interaction: discord.Interaction):
        try:
            val = float(self.val_input.value.replace(",", ".").strip())
            PRECOS_MANUAIS[self.currency][self.pack_slug] = val
            save_data()
            
            # Atualiza o Embed dinamicamente
            await atualizar_mensagem_precos()
            
            simbolo = "R$" if self.currency == "pt" else "USDT" if self.currency == "en" else "PHP"
            await interaction.response.send_message(f"✅ Preço manual do pacote **{self.pack_slug}** em **{self.currency.upper()}** atualizado para **{simbolo} {val:.2f}** com sucesso!", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Valor inválido. Digite um número correto (ex: 5.50).", ephemeral=True)

class PacoteSelectView(ui.View):
    def __init__(self, currency):
        super().__init__(timeout=120)
        self.currency = currency
        
        opts = [
            discord.SelectOption(label="Pack 1$", value="pack_1"),
            discord.SelectOption(label="Pack 3$", value="pack_3"),
            discord.SelectOption(label="Pack 5$", value="pack_5"),
            discord.SelectOption(label="Pack 10$", value="pack_10"),
            discord.SelectOption(label="Pack 30$", value="pack_30"),
            discord.SelectOption(label="Passe 40$", value="pack_40"),
            discord.SelectOption(label="Pack 50$", value="pack_50"),
            discord.SelectOption(label="Pack 100$", value="pack_100")
        ]
        s = ui.Select(placeholder="Selecione o pacote para alterar...", options=opts)
        s.callback = self.select_callback
        self.add_item(s)
        
    async def select_callback(self, interaction: discord.Interaction):
        pack_slug = interaction.data['values'][0]
        await interaction.response.send_modal(AlteraPrecoModal(self.currency, pack_slug))

class MoedaSelectView(ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        
    @ui.button(label="BRL R$ (Moeda Local)", style=discord.ButtonStyle.primary, emoji="🇧🇷")
    async def set_brl(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.edit_message(content="Selecione o pacote para alterar o valor em BRL (R$):", view=PacoteSelectView("pt"))
        
    @ui.button(label="USDT $ (Moeda Base)", style=discord.ButtonStyle.primary, emoji="🇺🇸")
    async def set_usdt(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.edit_message(content="Selecione o pacote para alterar o valor em USDT ($):", view=PacoteSelectView("en"))
        
    @ui.button(label="PHP ₱ (Pinoy)", style=discord.ButtonStyle.primary, emoji="🇵🇭")
    async def set_php(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.edit_message(content="Selecione o pacote para alterar o valor em PHP (₱):", view=PacoteSelectView("ph"))

class TabelaPrecosView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        
    @ui.button(label="🛒 Comprar / Buy", style=discord.ButtonStyle.success, emoji="🛒", custom_id="tabela_comprar_btn")
    async def comprar(self, interaction: discord.Interaction, button: ui.Button):
        # Evita a abertura de mais de um ticket simultâneo
        user_name = interaction.user.name.lower()
        for thread in interaction.guild.threads:
            if thread.name.startswith(("🛒-", "💰-", "💎-", "💬-")) and f"-{user_name}" in thread.name.lower() and not thread.archived:
                return await interaction.response.send_message("❌ Já possui um ticket aberto!", ephemeral=True)
        
        try:
            thread = await interaction.channel.create_thread(name=f"🛒-buy-{interaction.user.name.lower()}", auto_archive_duration=60, type=discord.ChannelType.private_thread)
            await thread.add_user(interaction.user)
            await interaction.response.send_message(f"Ticket aberto: {thread.mention}", ephemeral=True)
            v = ui.View()
            async def set_lang(i, lang):
                preferencia_idioma[str(thread.id)] = lang
                db.save_service_config(thread.id, interaction.user.id, lang)
                save_data()
                adm_id = IDS_CARGOS_ADM[0] if IDS_CARGOS_ADM else 0
                embed_start = discord.Embed(description=get_text(thread.id, "alerta_staff").format(user=interaction.user.mention, adm=adm_id), color=0x2ecc71)
                embed_jogo = discord.Embed(title=get_text(thread.id, "escolha_jogo_titulo"), description=get_text(thread.id, "escolha_jogo_desc"), color=0x2ecc71)
                embed_jogo.set_image(url=IMAGENS_JOGOS["default"])
                await i.response.edit_message(content=f"<@&{adm_id}>", embeds=[embed_start, embed_jogo], view=EscolhaJogo(thread.id))
                
                # Salva o estado inicial do canal
                orig_msg = await i.original_response()
                db.set_internal_data(f"view_state_{thread.id}", json.dumps({
                    "type": "EscolhaJogo",
                    "message_id": orig_msg.id,
                    "user_id": interaction.user.id
                }))
            
            b1 = ui.Button(label="Português", emoji="🇧🇷")
            b1.callback = lambda i: set_lang(i, "pt")
            b2 = ui.Button(label="English", emoji="🇺🇸")
            b2.callback = lambda i: set_lang(i, "en")
            v.add_item(b1).add_item(b2)
            await thread.send(embed=discord.Embed(title="🌐 Language", description="**Selecione o idioma\n Choose your Language:**", color=0x9b59b6), view=v)
        except Exception as e:
            print(f"Erro ao abrir ticket de compra pela tabela: {e}")

    @ui.button(label="⚙️ Alterar Preço", style=discord.ButtonStyle.secondary, emoji="⚙️", custom_id="tabela_admin_preco_btn")
    async def alterar_preco(self, interaction: discord.Interaction, button: ui.Button):
        if not has_admin_permission(interaction.user):
            return await interaction.response.send_message("❌ Apenas Administradores podem usar este painel de preços!", ephemeral=True)
        
        await interaction.response.send_message("⚙️ **Painel de Alteração de Preços**\nEscolha qual moeda você deseja atualizar:", view=MoedaSelectView(), ephemeral=True)


# --- FIM DO GERADOR ---

def criar_embed_resumo(canal_id, instr_pag=None):
    c_id = str(canal_id)
    dados = carrinhos.get(c_id)
    lang = preferencia_idioma.get(c_id, "pt")
    jogo = preferencia_jogo.get(c_id, "mir4")
    t = LANGUAGES[lang]
    if not dados or not dados['itens']:
        return discord.Embed(description="Carrinho vazio." if lang == "pt" else "Empty cart.", color=discord.Color.red())
    
    embed = discord.Embed(title=t["resumo_titulo"], color=0x2ecc71)
    itens_texto = "---\n\n"
    total_usdt_sem_cupom = 0
    total_bruto_local = 0 
    
    for idx, i in enumerate(dados['itens']):
        item_usdt = i['total_usdt_item']
        total_usdt_sem_cupom += item_usdt
        
        itens_texto += f"**{idx+1}.** {i.get('emoji', '💎')} **{i['nome']}**\n"
        
        if lang == "ph":
            p_manual = PRECOS_MANUAIS["ph"].get(i['nome_slug'])
            val_final_linha = (p_manual * i['qtd'] if p_manual else (i['total_usdt_item'] * bot.cotacao_php))
            total_bruto_local += val_final_linha
            itens_texto += f"{t['qtd_label']}: {i['qtd']} | {val_final_linha:.2f} PHP\n\n"
        elif lang == "en":
            p_manual = PRECOS_MANUAIS["en"].get(i['nome_slug'])
            val_final_linha = (p_manual * i['qtd']) if p_manual else item_usdt
            total_bruto_local += val_final_linha
            itens_texto += f"{t['qtd_label']}: {i['qtd']} | {val_final_linha:.2f} USDT\n\n"
        else:
            p_manual = PRECOS_MANUAIS["pt"].get(i['nome_slug'])
            if p_manual:
                val_final_linha = p_manual * i['qtd']
                total_bruto_local += val_final_linha
                itens_texto += f"{t['qtd_label']}: {i['qtd']} | R$ {val_final_linha:.2f}\n\n"
            else:
                val_final_linha = item_usdt * bot.cotacao_dolar
                total_bruto_local += val_final_linha
                itens_texto += f"{t['qtd_label']}: {i['qtd']} | R$ {val_final_linha:.2f}\n\n"
    
    cupom_code = dados.get("cupom")
    if cupom_code in CUPONS:
        perc = CUPONS[cupom_code]
        valor_desconto_usdt = total_usdt_sem_cupom * perc
        final_usdt = total_usdt_sem_cupom - valor_desconto_usdt
        
        valor_desconto_local = total_bruto_local * perc
        valor_final_local = total_bruto_local - valor_desconto_local
        
        simbolo = "PHP" if lang == "ph" else "USDT" if lang == "en" else "R$"
        
        design_cupom = (
            f"{t['resumo_cupom_titulo']} `{cupom_code}`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 {t['subtotal_label']}: {simbolo} **{total_bruto_local:.2f}**\n"
            f"📉 {t['desconto_label'].format(perc=perc*100)}: -{simbolo} **{valor_desconto_local:.2f}**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━"
        )
        itens_texto += f"--- \n{design_cupom}\n\n"
    else:
        valor_final_local = total_bruto_local
        final_usdt = total_usdt_sem_cupom

    if lang == "ph":
        itens_texto += f"**{t['total_usdt_label']}: {valor_final_local:.2f} PHP**"
    elif lang == "en":
        itens_texto += f"**{t['total_usdt_label']}: {valor_final_local:.2f} USDT**"
    else:
        itens_texto += f"**{t['total_brl_label']}: R$ {valor_final_local:.2f}**\n"
        itens_texto += f"**{t['total_usdt_label']}: {final_usdt:.2f} USDT**"
    
    if instr_pag:
        itens_texto += f"\n\n{instr_pag}"

    embed.description = itens_texto
    embed.set_footer(text=t["cotacao_footer"].format(val=bot.cotacao_dolar))
    embed.set_image(url=get_img(jogo))
    return embed

class FeedbackModal(ui.Modal):
    def __init__(self, lang):
        t = LANGUAGES[lang]
        super().__init__(title=t["feedback_modal_title"])
        self.lang = lang
        
        self.nota = ui.TextInput(
            label=t["feedback_modal_nota"], 
            placeholder=t["feedback_modal_placeholder_nota"], 
            min_length=1, max_length=2, required=True
        )
        self.comentario = ui.TextInput(
            label=t["feedback_modal_comment"], 
            style=discord.TextStyle.paragraph, 
            placeholder=t["feedback_modal_placeholder_comment"], 
            required=True
        )
        
        self.add_item(self.nota)
        self.add_item(self.comentario)

    async def on_submit(self, interaction: discord.Interaction):
        t = LANGUAGES[self.lang]
        canal_feedback = bot.get_channel(ID_CANAL_FEEDBACK)
        if canal_feedback:
            embed = discord.Embed(title="⭐ New Feedback Received", color=discord.Color.gold(), timestamp=datetime.datetime.now())
            embed.set_thumbnail(url=interaction.user.display_avatar.url)
            embed.add_field(name="👤 Client", value=f"{interaction.user.mention} (`{interaction.user.id}`)", inline=True)
            embed.add_field(name="📊 Rate", value=f"**{self.nota.value}/10**", inline=True)
            embed.add_field(name="💬 Commentary", value=self.comentario.value, inline=False)
            await canal_feedback.send(embed=embed)
        
        await interaction.response.send_message(t["feedback_success"], ephemeral=True)

class FeedbackDMView(ui.View):
    def __init__(self, lang):
        super().__init__(timeout=None)
        self.lang = lang
        t = LANGUAGES[lang]
        
        btn = ui.Button(label=t["feedback_btn_label"], style=discord.ButtonStyle.primary, emoji="⭐")
        btn.callback = self.feedback_button_callback
        self.add_item(btn)
    
    async def feedback_button_callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(FeedbackModal(self.lang))

class QuantidadeModal(ui.Modal):
    def __init__(self, canal_id, item_val):
        super().__init__(title=get_text(canal_id, "modal_qtd_titulo"))
        self.canal_id = str(canal_id)
        self.item_val = item_val
        if item_val == "gold":
            label = get_text(self.canal_id, "comprar_gold_label")
            placeholder = "Ex: 1000"
            max_l = 10 
        else:
            label = get_text(self.canal_id, "modal_qtd_label")
            placeholder = "Ex: 1"
            max_l = 4
        self.inp = ui.TextInput(label=label, placeholder=placeholder, default="1000" if item_val == "gold" else "1", min_length=1, max_length=max_l)
        self.add_item(self.inp)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            val_raw = self.inp.value.strip()
            val_raw = val_raw.replace(".", "").replace(",", "").replace(" ", "").replace("$", "")
            
            if not val_raw.isdigit():
                return await interaction.response.send_message(get_text(self.canal_id, "val_invalido"), ephemeral=True)
            
            qtd_input = int(val_raw)
            
            if self.item_val == "gold":
                if qtd_input < 1000 or qtd_input > 9999999: 
                    return await interaction.response.send_message(get_text(self.canal_id, "val_invalido"), ephemeral=True)
                
                total_item_usdt = 4.50 * (qtd_input / 1000)
                nome_item = get_text(self.canal_id, "item_gold_nome").format(val=qtd_input)
                emoji, real_qtd = "🪙", 1
                nome_slug = "gold_1k_usd"
            else:
                if qtd_input <= 0:
                    return await interaction.response.send_message(get_text(self.canal_id, "val_invalido"), ephemeral=True)
                
                p_manual = PRECOS_MANUAIS["en"].get(self.item_val)
                unit_usdt = p_manual if p_manual else precos_base_usd.get(self.item_val, 1.0)
                
                total_item_usdt = unit_usdt * qtd_input
                nome_item = f"Pack {self.item_val.split('_')[1]}$"
                emoji, real_qtd = "💎", qtd_input
                nome_slug = self.item_val
            
            if self.canal_id not in carrinhos: 
                carrinhos[self.canal_id] = {"itens": [], "cupom": None}
            
            item_existente = None
            for item in carrinhos[self.canal_id]["itens"]:
                if item["nome_slug"] == nome_slug:
                    item_existente = item
                    break
            
            if item_existente:
                if nome_slug == "gold_1k_usd":
                    try:
                        gold_atual = int(item_existente["nome"].split(" ")[1].replace(",", ""))
                        novo_gold = gold_atual + qtd_input
                        item_existente["nome"] = get_text(self.canal_id, "item_gold_nome").format(val=novo_gold)
                    except: pass
                else:
                    item_existente["qtd"] += real_qtd
                
                item_existente["total_usdt_item"] += total_item_usdt
            else:
                carrinhos[self.canal_id]["itens"].append({
                    "nome": nome_item, 
                    "nome_slug": nome_slug, 
                    "qtd": real_qtd, 
                    "total_usdt_item": total_item_usdt, 
                    "emoji": emoji
                })
            
            db.add_to_cart(self.canal_id, nome_slug, nome_item, real_qtd, total_item_usdt / real_qtd if real_qtd > 0 else 0)
            save_data()
            await interaction.response.edit_message(embed=criar_embed_resumo(self.canal_id), view=CarrinhoAcoes(self.canal_id))
            
            # Persiste o novo estado de CarrinhoAcoes
            orig_msg = await interaction.original_response()
            db.set_internal_data(f"view_state_{self.canal_id}", json.dumps({
                "type": "CarrinhoAcoes",
                "message_id": orig_msg.id,
                "user_id": interaction.user.id
            }))
        except Exception as e:
            print(f"Erro no modal: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(get_text(self.canal_id, "val_invalido"), ephemeral=True)

class DadosRecargaModal(ui.Modal):
    def __init__(self, canal_id):
        super().__init__(title=get_text(canal_id, "modal_titulo_dados"))
        self.canal_id = str(canal_id)
        self.login = ui.TextInput(label=get_text(self.canal_id, "modal_campo_login"), placeholder=get_text(self.canal_id, "modal_placeholder_login"), required=True)
        self.email = ui.TextInput(label=get_text(self.canal_id, "modal_campo_email"), placeholder="email@gmail.com", required=True)
        self.senha = ui.TextInput(label=get_text(self.canal_id, "modal_campo_senha"), placeholder="******", style=discord.TextStyle.short, required=True)
        self.char = ui.TextInput(label=get_text(self.canal_id, "modal_campo_char"), placeholder=get_text(self.canal_id, "modal_placeholder_char"), required=True)
        self.servidor = ui.TextInput(label=get_text(self.canal_id, "modal_campo_servidor"), placeholder=get_text(self.canal_id, "modal_placeholder_servidor"), required=True)
        self.add_item(self.login)
        self.add_item(self.email)
        self.add_item(self.senha)
        self.add_item(self.char)
        self.add_item(self.servidor)

    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed(title=get_text(self.canal_id, "embed_dados_titulo"), color=0x9b59b6, timestamp=datetime.datetime.now())
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.add_field(name=get_text(self.canal_id, "embed_dados_cliente"), value=interaction.user.mention, inline=False)
        embed.add_field(name=get_text(self.canal_id, "embed_dados_metodo"), value=f"`{self.login.value}`", inline=True)
        embed.add_field(name=get_text(self.canal_id, "embed_dados_email"), value=f"`{self.email.value}`", inline=True)
        embed.add_field(name=get_text(self.canal_id, "embed_dados_senha"), value=f"`{self.senha.value}`", inline=True)
        embed.add_field(name=get_text(self.canal_id, "embed_dados_char"), value=f"`{self.char.value}`", inline=True)
        embed.add_field(name=get_text(self.canal_id, "embed_dados_servidor"), value=f"`{self.servidor.value}`", inline=True)
        await interaction.response.send_message(get_text(self.canal_id, "dados_rece_bidos"), ephemeral=True)
        await interaction.channel.send(embed=embed)
        adm_id = IDS_CARGOS_ADM[0] if IDS_CARGOS_ADM else 0
        await interaction.channel.send(get_text(self.canal_id, "alerta_dados_preenchidos").format(adm=adm_id))

class StaffManualConcludeView(ui.View):
    def __init__(self, canal_id, cliente_id):
        super().__init__(timeout=None)
        self.canal_id = str(canal_id)
        self.cliente_id = cliente_id
        label_concluir = get_text(self.canal_id, "fechar_ticket_btn")
        btn_concluir = ui.Button(label=label_concluir, style=discord.ButtonStyle.success, emoji="✅", custom_id="manual_conclude_btn")
        btn_concluir.callback = self.concluir_callback
        self.add_item(btn_concluir)
        label_fechar = get_text(self.canal_id, "fechar_canal")
        btn_fechar = ui.Button(label=label_fechar, style=discord.ButtonStyle.danger, emoji="❌", custom_id="manual_cancel_btn")
        btn_fechar.callback = self.fechar_callback
        self.add_item(btn_fechar)

    async def concluir_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if not has_admin_permission(interaction.user):
            return await interaction.followup.send(get_text(self.canal_id, "erro_perm"), ephemeral=True)
        
        lang = preferencia_idioma.get(self.canal_id, "pt")
        guild = interaction.guild
        cliente = guild.get_member(self.cliente_id) or await guild.fetch_member(self.cliente_id)
        
        # --- GERAÇÃO DO TRANSCRIPT AO CONCLUIR ---
        transcript_content = await generate_transcript(interaction.channel)
        canal_logs = bot.get_channel(ID_CANAL_TRANSCRIPT_LOGS)
        if canal_logs and transcript_content:
            file_obj = io.BytesIO(transcript_content.encode('utf-8'))
            discord_file = discord.File(file_obj, filename=f"transcript_{self.canal_id}.html")
            log_embed = discord.Embed(
                title="📄 Ticket Concluído (Transcript)", 
                description=f"O ticket **{interaction.channel.name}** foi finalizado por {interaction.user.mention}.",
                color=discord.Color.blue(),
                timestamp=datetime.datetime.now()
            )
            await canal_logs.send(embed=log_embed, file=discord_file)

        if cliente:
            try:
                t = LANGUAGES[lang]
                embed_feedback = discord.Embed(title=t["feedback_dm_title"], description=t["feedback_dm_desc"], color=discord.Color.green())
                await cliente.send(embed=embed_feedback, view=FeedbackDMView(lang))
            except: pass
            
        await interaction.followup.send(get_text(self.canal_id, "encerrando"))
        await asyncio.sleep(3)
        await interaction.channel.delete()

    async def fechar_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.cliente_id and not has_admin_permission(interaction.user):
            return await interaction.response.send_message(get_text(self.canal_id, "erro_perm"), ephemeral=True)
        await interaction.response.send_message(get_text(self.canal_id, "encerrando"))
        await asyncio.sleep(3)
        await interaction.channel.delete()

class AprovadoFormView(ui.View):
    def __init__(self, canal_id):
        super().__init__(timeout=None)
        self.canal_id = str(canal_id)
        label_btn = get_text(self.canal_id, "btn_preencher")
        btn = ui.Button(label=label_btn, style=discord.ButtonStyle.primary, emoji="📝", custom_id="ticket_fill_data")
        btn.callback = self.preencher_callback
        self.add_item(btn)
        label_concluir = get_text(self.canal_id, "fechar_ticket_btn")
        btn_concluir = ui.Button(label=label_concluir, style=discord.ButtonStyle.success, emoji="✅", custom_id="ticket_conclude_admin")
        btn_concluir.callback = self.concluir_ticket_callback
        self.add_item(btn_concluir)

    async def preencher_callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(DadosRecargaModal(self.canal_id))

    async def concluir_ticket_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if not has_admin_permission(interaction.user):
            return await interaction.followup.send(get_text(self.canal_id, "erro_perm"), ephemeral=True)
        
        # --- GERAÇÃO DO TRANSCRIPT AO CONCLUIR ENTREGA ---
        transcript_content = await generate_transcript(interaction.channel)
        canal_logs = bot.get_channel(ID_CANAL_TRANSCRIPT_LOGS)
        if canal_logs and transcript_content:
            file_obj = io.BytesIO(transcript_content.encode('utf-8'))
            discord_file = discord.File(file_obj, filename=f"transcript_{self.canal_id}.html")
            log_embed = discord.Embed(
                title="📄 Entrega Concluída (Transcript)", 
                description=f"O canal de entrega **{interaction.channel.name}** foi finalizado por {interaction.user.mention}.",
                color=discord.Color.green(),
                timestamp=datetime.datetime.now()
            )
            await canal_logs.send(embed=log_embed, file=discord_file)

        cliente = None
        lang = preferencia_idioma.get(self.canal_id, "pt")
        try:
            footer_text = interaction.message.embeds[0].footer.text
            client_id = int(footer_text.replace("ID: ", ""))
            cliente = interaction.guild.get_member(client_id) or await interaction.guild.fetch_member(client_id)
        except:
            members = await interaction.channel.fetch_members()
            for m in members:
                member = interaction.guild.get_member(m.id)
                if member and not member.bot and not has_admin_permission(member):
                    cliente = member
                    break
        if cliente:
            try:
                t = LANGUAGES[lang]
                embed_feedback = discord.Embed(title=t["feedback_dm_title"], description=t["feedback_dm_desc"], color=discord.Color.green())
                await cliente.send(embed=embed_feedback, view=FeedbackDMView(lang))
            except: pass 
        await interaction.followup.send(get_text(self.canal_id, "encerrando"))
        await asyncio.sleep(3)
        await interaction.channel.delete()

class StaffCheckout(ui.View):
    def __init__(self, user_id, channel_id, valor_usdt, method):
        super().__init__(timeout=None)
        self.user_id, self.channel_id, self.valor_usdt, self.method = user_id, str(channel_id), valor_usdt, method

    @ui.button(label="Approve Payment", style=discord.ButtonStyle.success, emoji="✅")
    async def aprovar(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not has_admin_permission(interaction.user):
            return await interaction.followup.send("❌ Apenas Admin!", ephemeral=True)
        guild = interaction.guild
        
        # --- CORREÇÃO IMPORTANTE: Garantir o ID do cliente real do Banco de Dados para evitar o bug de pegar o ID do ADM ---
        config = db.get_service_config(self.channel_id)
        db_user_id = int(config[2]) if config else self.user_id
        
        cliente = guild.get_member(db_user_id) or await guild.fetch_member(db_user_id)
        if not cliente:
            return await interaction.followup.send("❌ O utilizador saiu.", ephemeral=True)
        
        for t_existente in guild.threads:
            if "-approved-" in t_existente.name.lower() and f"-{cliente.name.lower()}" in t_existente.name.lower() and not t_existente.archived:
                return await interaction.followup.send(f"❌ User {cliente.mention} already have one ticket opened!", ephemeral=True)

        lang = preferencia_idioma.get(self.channel_id, "pt")
        bandeira = "🇧🇷" if lang == "pt" else "🇺🇸" if lang == "en" else "🇵🇭"
        
        try:
            dados_carrinho = carrinhos.get(self.channel_id)
            cupom_utilizado = dados_carrinho.get("cupom") if dados_carrinho else None
            desconto_perc = CUPONS.get(cupom_utilizado, 0) if cupom_utilizado else 0
            
            v_total_local = 0
            v_total_brl_accurate = 0
            v_total_php_accurate = 0
            lista_itens_log = ""
            
            if dados_carrinho:
                for it in dados_carrinho['itens']:
                    if lang == "ph":
                        m_p = PRECOS_MANUAIS["ph"].get(it['nome_slug'])
                        v_total_local += (m_p * it['qtd'] if m_p else (it['total_usdt_item'] * bot.cotacao_php))
                    else:
                        m_p = PRECOS_MANUAIS["pt"].get(it['nome_slug'])
                        v_total_local += (m_p * it['qtd'] if m_p else (it['total_usdt_item'] * bot.cotacao_dolar))
                    
                    m_p_pt = PRECOS_MANUAIS["pt"].get(it['nome_slug'])
                    v_total_brl_accurate += (m_p_pt * it['qtd'] if m_p_pt else (it['total_usdt_item'] * bot.cotacao_dolar))

                    m_p_ph = PRECOS_MANUAIS["ph"].get(it['nome_slug'])
                    v_total_php_accurate += (m_p_ph * it['qtd'] if m_p_ph else (it['total_usdt_item'] * bot.cotacao_php))
                    
                    # Gerar string de itens para o log
                    lista_itens_log += f"• {it.get('emoji', '📦')} **{it['nome']}** (x{it['qtd']})\n"
                
                v_total_local *= (1 - desconto_perc)
                v_total_brl_accurate *= (1 - desconto_perc)
                v_total_php_accurate *= (1 - desconto_perc)
            else:
                v_total_local = self.valor_usdt * (bot.cotacao_php if lang == "ph" else bot.cotacao_dolar)
                v_total_brl_accurate = self.valor_usdt * bot.cotacao_dolar
                v_total_php_accurate = self.valor_usdt * bot.cotacao_php
                lista_itens_log = "• 📦 Pacote Único (Valor Manual)"

            canal_logs = bot.get_channel(ID_CANAL_LOGS)
            if canal_logs:
                # DESIGN MELHORADO DOS LOGS
                log_embed = discord.Embed(title="💰 NEW PAYMENT APPROVED", color=0x2ecc71, timestamp=datetime.datetime.now())
                log_embed.set_thumbnail(url="https://media.discordapp.net/attachments/1492912195049095208/1493481923752759357/2889edc1-1a70-456a-a32c-e3f050102347.png")
                
                log_embed.add_field(name="👤 CLIENT", value=f"{cliente.mention}\n`ID: {cliente.id}`", inline=True)
                log_embed.add_field(name="💳 METHOD", value=f"**{self.method}**", inline=True)
                
                # Mostrar os itens comprados no log
                log_embed.add_field(name="📦 PACKAGES BOUGHT", value=lista_itens_log or "Nenhum item detectado", inline=False)
                
                valor_display = f"**{v_total_local:.2f} PHP**" if lang == "ph" else f"**R$ {v_total_local:.2f}**"
                if lang == "en":
                    valor_display = f"**{self.valor_usdt:.2f} USDT**"
                
                log_embed.add_field(name="💵 VALUE PAID", value=valor_display, inline=True)
                log_embed.add_field(name="🪙 TOTAL USDT", value=f"**{self.valor_usdt:.2f} USDT**", inline=True)
                log_embed.add_field(name="🎟️ COUPON", value=f"`{cupom_utilizado or 'NENHUM'}`", inline=True)
                
                log_embed.set_footer(text=f"📜 Ticket Log System • ID Canal: {self.channel_id}")
                await canal_logs.send(embed=log_embed)
            
            await interaction.edit_original_response(content=f"✅ **Payment approved for {cliente.mention}**", view=None, embed=None)
            
            ticket_channel = guild.get_thread(int(self.channel_id)) or guild.get_channel(int(self.channel_id))
            if ticket_channel:
                await ticket_channel.send(content=f"✅ **{get_text(ticket_channel.id, 'pagamento_aprovado')}**\n{get_text(ticket_channel.id, 'encerrando')}")

            spawn_channel = ticket_channel.parent if isinstance(ticket_channel, discord.Thread) else ticket_channel
            novo_topico = await spawn_channel.create_thread(name=f"{bandeira}-approved-{cliente.name.lower()}", auto_archive_duration=60, type=discord.ChannelType.private_thread)
            
            await novo_topico.add_user(cliente)
            await novo_topico.add_user(interaction.user)
            
            preferencia_idioma[str(novo_topico.id)] = lang
            db.save_service_config(novo_topico.id, cliente.id, lang)
            
            # --- MUDANÇA: Voltando a marcar o Admin de forma visível ---
            adm_id = IDS_CARGOS_ADM[0] if IDS_CARGOS_ADM else 0
            await novo_topico.send(f"<@&{adm_id}>")
            
            lista_itens_msg = ""
            if dados_carrinho and dados_carrinho['itens']:
                lista_itens_msg = "> " + get_text(self.channel_id, "resumo_compra_staff") + "\n"
                for it in dados_carrinho['itens']:
                    lista_itens_msg += f"> • {it.get('emoji', '📦')} **{it['nome']}** (x{it['qtd']})\n"
            
            embed_final = discord.Embed(title=get_text(novo_topico.id, "form_entrega_titulo"), description=get_text(novo_topico.id, "form_entrega_desc").format(itens=lista_itens_msg), color=0x2ecc71)
            embed_final.set_footer(text=f"ID: {cliente.id}")
            
            msg = await novo_topico.send(f"Welcome {cliente.mention}!", embed=embed_final, view=AprovadoFormView(novo_topico.id))
            
            # Persiste o estado da tela de entrega
            db.set_internal_data(f"view_state_{novo_topico.id}", json.dumps({
                "type": "AprovadoFormView",
                "message_id": msg.id,
                "user_id": cliente.id
            }))
            
            # --- ATUALIZAÇÃO DAS ESTATÍSTICAS ---
            await update_sales_stats(cliente.name, v_total_brl_accurate, self.valor_usdt, v_total_php_accurate, cupom_utilizado)
            
            await asyncio.sleep(5)
            if ticket_channel: await ticket_channel.delete()
        except Exception as e:
            print(f"Erro ao aprovar: {e}")
            await interaction.followup.send(f"❌ Erro ao criar entrega: {e}", ephemeral=True)

    @ui.button(label="Recusar", style=discord.ButtonStyle.danger, emoji="❌")
    async def recusar(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not has_admin_permission(interaction.user):
            return await interaction.followup.send("❌ Apenas Admin!", ephemeral=True)
        
        guild = interaction.guild
        
        # --- CORREÇÃO IMPORTANTE: Garantir o ID do cliente real do Banco de Dados ---
        config = db.get_service_config(self.channel_id)
        db_user_id = int(config[2]) if config else self.user_id
        
        cliente = guild.get_member(db_user_id) or await guild.fetch_member(db_user_id)
        
        thread = bot.get_channel(int(self.channel_id))
        if thread: await thread.send(get_text(self.channel_id, "pagamento_recusado"))
        
        await interaction.edit_original_response(content=f"❌ Pagamento Rejeitado para {cliente.mention if cliente else 'Utilizador'}", view=None, embed=None)

class StatusAnaliseUserView(ui.View):
    def __init__(self, user_id, channel_id):
        super().__init__(timeout=None)
        self.user_id, self.channel_id = user_id, channel_id
        lang = preferencia_idioma.get(channel_id, "pt")
        t = LANGUAGES[lang]
        
        btn_voltar = ui.Button(label=t["voltar_btn"], style=discord.ButtonStyle.secondary, emoji="🔙")
        btn_voltar.callback = self.voltar_callback
        self.add_item(btn_voltar)
        
        btn_cancelar = ui.Button(label=t["fechar_canal"], style=discord.ButtonStyle.danger, emoji="❌")
        btn_cancelar.callback = self.cancelar_callback
        self.add_item(btn_cancelar)

    async def voltar_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id: return
        await interaction.response.edit_message(content=None, embed=criar_embed_resumo(self.channel_id), view=CarrinhoAcoes(self.channel_id))
        
        # Persiste o retorno
        orig_msg = await interaction.original_response()
        db.set_internal_data(f"view_state_{self.channel_id}", json.dumps({
            "type": "CarrinhoAcoes",
            "message_id": orig_msg.id,
            "user_id": self.user_id
        }))

    async def cancelar_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id: return
        await interaction.response.send_message(get_text(self.channel_id, "encerrando"))
        await asyncio.sleep(3)
        await interaction.channel.delete()

class UserConfirmPaymentView(ui.View):
    def __init__(self, user_id, channel_id, final_usdt, method_name, instr_pag):
        super().__init__(timeout=None)
        self.user_id, self.channel_id, self.final_usdt, self.method_name, self.instr_pag = user_id, channel_id, final_usdt, method_name, instr_pag
        
        label_confirm = get_text(channel_id, "confirmar_pag_btn")
        btn_confirm = ui.Button(label=label_confirm, style=discord.ButtonStyle.success, emoji="✅")
        btn_confirm.callback = self.confirm_callback
        self.add_item(btn_confirm)
        
        label_voltar = get_text(channel_id, "voltar_btn")
        btn_voltar = ui.Button(label=label_voltar, style=discord.ButtonStyle.secondary, emoji="🔙")
        btn_voltar.callback = self.back_callback
        self.add_item(btn_voltar)

    async def confirm_callback(self, interaction: discord.Interaction):
        # --- CORREÇÃO IMPORTANTE: Obter o ID de cliente original do Banco de Dados ---
        config = db.get_service_config(self.channel_id)
        db_user_id = int(config[2]) if config else self.user_id

        if interaction.user.id != db_user_id:
            return await interaction.response.send_message("❌ Apenas o dono do ticket pode confirmar!", ephemeral=True)
        
        t = LANGUAGES[preferencia_idioma.get(self.channel_id, "pt")]
        adm_id = IDS_CARGOS_ADM[0] if IDS_CARGOS_ADM else 0
        mention = f"<@&{adm_id}>" if adm_id else "@ADMIN"
        
        embed_user = discord.Embed(title=f"⏳ {t['status_analise_titulo']}", description=t["aguardando_staff"], color=discord.Color.orange())
        embed_user.set_image(url=get_img(preferencia_jogo.get(self.channel_id, "mir4")))
        await interaction.response.edit_message(content=None, embed=embed_user, view=StatusAnaliseUserView(db_user_id, self.channel_id))
        
        # Persiste o novo estado de análise
        orig_msg = await interaction.original_response()
        db.set_internal_data(f"view_state_{self.channel_id}", json.dumps({
            "type": "StatusAnaliseUserView",
            "message_id": orig_msg.id,
            "user_id": db_user_id
        }))
        
        canal_staff = bot.get_channel(ID_CANAL_PAYMENT)
        if canal_staff:
            embed_staff = criar_embed_resumo(self.channel_id, instr_pag=self.instr_pag)
            embed_staff.title = f"💸 {t['status_analise_titulo']}"
            embed_staff.set_thumbnail(url="https://media.discordapp.net/attachments/1492912195049095208/1493481923752759357/2889edc1-1a70-456a-a32c-e3f050102347.png")
            embed_staff.add_field(name="👤 Utilizador:", value=f"<@{db_user_id}>", inline=True)
            embed_staff.add_field(name="💳 Método:", value=self.method_name, inline=True)
            embed_staff.add_field(name="💬 Canal:", value=interaction.channel.mention, inline=False)
            
            await canal_staff.send(content=mention, embed=embed_staff, view=StaffCheckout(db_user_id, self.channel_id, self.final_usdt, self.method_name))

    async def back_callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content=None, embed=criar_embed_resumo(self.channel_id), view=CarrinhoAcoes(self.channel_id))
        
        # Salva o estado ao retornar ao carrinho
        orig_msg = await interaction.original_response()
        db.set_internal_data(f"view_state_{self.channel_id}", json.dumps({
            "type": "CarrinhoAcoes",
            "message_id": orig_msg.id,
            "user_id": self.user_id
        }))

class CarrinhoAcoes(ui.View):
    def __init__(self, canal_id):
        super().__init__(timeout=None)
        self.canal_id = str(canal_id)
        self.lang = preferencia_idioma.get(self.canal_id, "pt")
        t = LANGUAGES[self.lang]
        self.add_item(ui.Button(label=t["finalizar_btn"], style=discord.ButtonStyle.success, emoji="✅", custom_id="ticket_finish"))
        self.add_item(ui.Button(label=t["add_mais_btn"], style=discord.ButtonStyle.primary, emoji="➕", custom_id="ticket_add_more"))
        self.add_item(ui.Button(label=t["remover_btn"], style=discord.ButtonStyle.secondary, emoji="🗑️", custom_id="ticket_remove_item"))
        self.add_item(ui.Button(label=t["cupom_btn"], style=discord.ButtonStyle.secondary, emoji="🏷️", custom_id="ticket_coupon"))
        self.add_item(ui.Button(label=t["fechar_canal"], style=discord.ButtonStyle.danger, emoji="❌", custom_id="ticket_cancel"))
    
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        cid = interaction.data.get("custom_id")
        if cid == "ticket_finish": await self.finalizar(interaction)
        elif cid == "ticket_add_more": await self.add(interaction)
        elif cid == "ticket_remove_item": await self.rem(interaction)
        elif cid == "ticket_coupon": await interaction.response.send_modal(CupomModal(self.canal_id))
        elif cid == "ticket_cancel": await self.fechar(interaction)
        return False

    async def mostrar_instrucoes_pagamento(self, interaction, final_usdt, method_name, instr_pag):
        embed_unificada = criar_embed_resumo(self.canal_id, instr_pag=instr_pag)
        
        # --- CORREÇÃO IMPORTANTE: Obter o ID de cliente real do Banco de Dados para evitar o ID do ADM ---
        config = db.get_service_config(self.canal_id)
        db_user_id = int(config[2]) if config else interaction.user.id
        
        view_confirmacao = UserConfirmPaymentView(db_user_id, self.canal_id, final_usdt, method_name, instr_pag)
        await interaction.response.edit_message(content=None, embed=embed_unificada, view=view_confirmacao)
        
        # Persiste o estado da tela de confirmação de pagamento
        orig_msg = await interaction.original_response()
        db.set_internal_data(f"view_state_{self.canal_id}", json.dumps({
            "type": "UserConfirmPaymentView",
            "message_id": orig_msg.id,
            "user_id": db_user_id,
            "final_usdt": final_usdt,
            "method_name": method_name,
            "instr_pag": instr_pag
        }))
        
    async def finalizar(self, interaction: discord.Interaction):
        dados = carrinhos.get(self.canal_id)
        if not dados: return
        
        desconto_perc = CUPONS.get(dados.get("cupom"), 0)
        total_usdt_carrinho = sum(i['total_usdt_item'] for i in dados['itens'])
        final_usdt = total_usdt_carrinho * (1 - desconto_perc)
        
        t = LANGUAGES[self.lang]
        view_pag = ui.View()
        btn_back_main = ui.Button(label=t["voltar_btn"], style=discord.ButtonStyle.secondary, emoji="🔙")
        async def back_main_cb(i2: discord.Interaction):
            await i2.response.edit_message(content=None, embed=criar_embed_resumo(self.canal_id), view=CarrinhoAcoes(self.canal_id))
            # Reseta estado de volta ao carrinho ativo
            orig_msg = await i2.original_response()
            db.set_internal_data(f"view_state_{self.canal_id}", json.dumps({
                "type": "CarrinhoAcoes",
                "message_id": orig_msg.id,
                "user_id": interaction.user.id
            }))
        btn_back_main.callback = back_main_cb
        
        if self.lang == "ph":
            total_php = 0
            for it in dados['itens']:
                m_p = PRECOS_MANUAIS["ph"].get(it['nome_slug'])
                total_php += (m_p * it['qtd'] if m_p else (it['total_usdt_item'] * bot.cotacao_php))
            total_php *= (1 - desconto_perc)

            btn_binance_wallet = ui.Button(label="Binance (Wallet BEP20)", style=discord.ButtonStyle.primary, emoji="🔶")
            btn_binance_id = ui.Button(label="Binance Pay (ID)", style=discord.ButtonStyle.primary, emoji="🆔")
            btn_gcash = ui.Button(label="GCash (PHP)", style=discord.ButtonStyle.success, emoji="📱")
            
            async def binance_wallet_cb(i):
                instr = f"🔶 **Wallet (BEP20):** `{CARTEIRA_USDT_BEP20}`\n💰 **Value:** `{final_usdt:.2f} USDT`"
                await self.mostrar_instrucoes_pagamento(i, final_usdt, "Binance Wallet", instr)
            async def binance_id_cb(i):
                instr = f"🔶 **Binance Pay ID:** `{BINANCE_PAY_ID}`\n💰 **Value:** `{final_usdt:.2f} USDT`"
                await self.mostrar_instrucoes_pagamento(i, final_usdt, "Binance ID", instr)
            async def gcash_cb(i):
                instr = f"💠 **GCash Number:** `{CHAVE_GCASH}`\n💰 **Value:** `{total_php:.2f} PHP`"
                await self.mostrar_instrucoes_pagamento(i, final_usdt, "GCash", instr)
            
            btn_binance_wallet.callback = binance_wallet_cb
            btn_binance_id.callback = binance_id_cb
            btn_gcash.callback = gcash_cb
            view_pag.add_item(btn_binance_wallet).add_item(btn_binance_id).add_item(btn_gcash).add_item(btn_back_main)
            
        elif self.lang == "pt":
            total_brl = 0
            for it in dados['itens']:
                m_p = PRECOS_MANUAIS["pt"].get(it['nome_slug'])
                total_brl += (m_p * it['qtd'] if m_p else (it['total_usdt_item'] * bot.cotacao_dolar))
            total_brl *= (1 - desconto_perc)

            btn_pix = ui.Button(label="PIX", style=discord.ButtonStyle.success, emoji="💠")
            btn_binance_wallet = ui.Button(label="Binance (Wallet)", style=discord.ButtonStyle.primary, emoji="🔶")
            btn_binance_id = ui.Button(label="Binance Pay (ID)", style=discord.ButtonStyle.primary, emoji="🆔")
            
            async def pix_cb(i):
                instr = f"💠 **Chave PIX:** `{CHAVE_PIX}`\n💰 **Valor:** `R$ {total_brl:.2f}`"
                await self.mostrar_instrucoes_pagamento(i, final_usdt, "PIX", instr)
            async def binance_wallet_cb(i):
                instr = f"🔶 **Wallet (BEP20):** `{CARTEIRA_USDT_BEP20}`\n💰 **Valor:** `{final_usdt:.2f} USDT`"
                await self.mostrar_instrucoes_pagamento(i, final_usdt, "Binance Wallet", instr)
            async def binance_id_cb(i):
                instr = f"🔶 **Binance Pay ID:** `{BINANCE_PAY_ID}`\n💰 **Valor:** `{final_usdt:.2f} USDT`"
                await self.mostrar_instrucoes_pagamento(i, final_usdt, "Binance ID", instr)
            
            btn_pix.callback = pix_cb
            btn_binance_wallet.callback = binance_wallet_cb
            btn_binance_id.callback = binance_id_cb
            view_pag.add_item(btn_pix).add_item(btn_binance_wallet).add_item(btn_binance_id).add_item(btn_back_main)
        else: 
            btn_binance_wallet = ui.Button(label="Binance (Wallet)", style=discord.ButtonStyle.primary, emoji="🔶")
            btn_binance_id = ui.Button(label="Binance Pay (ID)", style=discord.ButtonStyle.primary, emoji="🆔")
            
            async def binance_wallet_cb_en(i):
                instr = f"🔶 **Wallet (BEP20):** `{CARTEIRA_USDT_BEP20}`\n💰 **Value:** `{final_usdt:.2f} USDT`"
                await self.mostrar_instrucoes_pagamento(i, final_usdt, "Binance Wallet", instr)
            async def binance_id_cb_en(i):
                instr = f"🔶 **Binance Pay ID:** `{BINANCE_PAY_ID}`\n💰 **Value:** `{final_usdt:.2f} USDT`"
                await self.mostrar_instrucoes_pagamento(i, final_usdt, "Binance ID", instr)
                
            btn_binance_wallet.callback = binance_wallet_cb_en
            btn_binance_id.callback = binance_id_cb_en
            view_pag.add_item(btn_binance_wallet).add_item(btn_binance_id).add_item(btn_back_main)
        
        msg_content = f"{get_text(self.canal_id, 'metodo_desc')}\n\n**{get_text(self.canal_id, 'aviso_adm_pagamento')}**"
        await interaction.response.edit_message(content=msg_content, view=view_pag, embed=criar_embed_resumo(self.canal_id))

    async def add(self, interaction: discord.Interaction):
        jogo = preferencia_jogo.get(self.canal_id, "mir4")
        t = LANGUAGES[self.lang]
        embed = discord.Embed(title=t["loja_titulo"].format(jogo.upper()), description=t["loja_desc"].format(jogo.upper()), color=0x3498db)
        embed.set_image(url=get_img(jogo))
        await interaction.response.edit_message(embed=embed, view=SelecaoItens(self.canal_id))
        
        # Persiste o estado da seleção de itens
        orig_msg = await interaction.original_response()
        db.set_internal_data(f"view_state_{self.canal_id}", json.dumps({
            "type": "SelecaoItens",
            "message_id": orig_msg.id,
            "user_id": interaction.user.id
        }))
    
    async def rem(self, interaction: discord.Interaction):
        if self.canal_id not in carrinhos or not carrinhos[self.canal_id]["itens"]:
            return await interaction.response.send_message(get_text(self.canal_id, "resumo_titulo"), ephemeral=True)
        itens = carrinhos[self.canal_id]["itens"]
        opts = [discord.SelectOption(label=f"{idx+1}. {i['nome']}", value=str(idx)) for idx, i in enumerate(itens)]
        v = ui.View()
        s = ui.Select(placeholder=get_text(self.canal_id, "placeholder_remover"), options=opts)
        async def s_cb(i):
            idx = int(s.values[0])
            carrinhos[self.canal_id]["itens"].pop(idx)
            save_data()
            await i.response.edit_message(embed=criar_embed_resumo(self.canal_id), view=CarrinhoAcoes(self.canal_id))
            # Persiste volta ao resumo do carrinho
            orig_msg = await i.original_response()
            db.set_internal_data(f"view_state_{self.canal_id}", json.dumps({
                "type": "CarrinhoAcoes",
                "message_id": orig_msg.id,
                "user_id": i.user.id
            }))
        s.callback = s_cb
        v.add_item(s)
        await interaction.response.edit_message(view=v)
    
    async def fechar(self, interaction: discord.Interaction):
        await interaction.response.send_message(get_text(self.canal_id, "encerrando"))
        await asyncio.sleep(3)
        await interaction.channel.delete()

class SelecaoItens(ui.View):
    def __init__(self, canal_id):
        super().__init__(timeout=None)
        self.canal_id = str(canal_id)
        opts = [
            discord.SelectOption(label="Pack 1$", value="pack_1", emoji="💎"), 
            discord.SelectOption(label="Pack 3$", value="pack_3", emoji="💎"), 
            discord.SelectOption(label="Pack 5$", value="pack_5", emoji="💎"), 
            discord.SelectOption(label="Pack 10$", value="pack_10", emoji="💎"), 
            discord.SelectOption(label="Pack 30$", value="pack_30", emoji="💎"), 
            discord.SelectOption(label="Pack 50$", value="pack_50", emoji="💎"), 
            discord.SelectOption(label="Pack 100$", value="pack_100", emoji="💎")
        ]
        s = ui.Select(placeholder=get_text(self.canal_id, "placeholder_item"), options=opts, custom_id="ticket_item_select")
        async def s_cb(interaction): 
            await interaction.response.send_modal(QuantidadeModal(self.canal_id, s.values[0]))
        s.callback = s_cb
        self.add_item(s)
        
        btn_cancel = ui.Button(label=get_text(self.canal_id, "fechar_canal"), style=discord.ButtonStyle.danger, emoji="❌", custom_id="ticket_cancel_select")
        async def cancel_cb(i):
            await i.response.send_message(get_text(self.canal_id, "encerrando"))
            await asyncio.sleep(3)
            await i.channel.delete()
        btn_cancel.callback = cancel_cb
        self.add_item(btn_cancel)

class EscolhaJogo(ui.View):
    def __init__(self, canal_id):
        super().__init__(timeout=None)
        self.canal_id = str(canal_id)
        options = [discord.SelectOption(label="MIR4", value="mir4", emoji="🐉")]
        s = ui.Select(placeholder=get_text(self.canal_id, "placeholder_jogo"), options=options, custom_id="ticket_game_select")
        async def s_cb(interaction):
            preferencia_jogo[self.canal_id] = s.values[0]
            
            # --- CORREÇÃO IMPORTANTE: Preservar o ID do cliente original e evitar substituir pelo ID do Admin ---
            orig_config = db.get_service_config(self.canal_id)
            orig_user_id = orig_config[2] if orig_config else interaction.user.id
            
            db.save_service_config(self.canal_id, orig_user_id, preferencia_idioma.get(self.canal_id, 'pt'), s.values[0])
            save_data()
            lang = preferencia_idioma.get(self.canal_id, "pt")
            desc_base = get_text(self.canal_id, "loja_desc").format(s.values[0].upper())
            desc_full = desc_base + (f"\n\n**{get_text(self.canal_id, 'cotacao_label')}:** R$ {bot.cotacao_dolar:.2f}" if lang != "ph" else "")
            embed = discord.Embed(title=get_text(self.canal_id, "loja_titulo").format(s.values[0].upper()), description=desc_full, color=0x3498db)
            embed.set_image(url=get_img(s.values[0]))
            await interaction.response.edit_message(embed=embed, view=SelecaoItens(self.canal_id))
            
            # Persiste novo estado de seleção de itens
            orig_msg = await interaction.original_response()
            db.set_internal_data(f"view_state_{self.canal_id}", json.dumps({
                "type": "SelecaoItens",
                "message_id": orig_msg.id,
                "user_id": orig_user_id
            }))
        s.callback = s_cb
        self.add_item(s)
        
        btn_cancelar = ui.Button(label=get_text(self.canal_id, "cancelar_compra_btn"), style=discord.ButtonStyle.danger, custom_id="ticket_cancel_game")
        async def cancel_cb(i):
            await i.response.send_message(get_text(self.canal_id, "encerrando"))
            await asyncio.sleep(4)
            await i.channel.delete()
        btn_cancelar.callback = cancel_cb
        self.add_item(btn_cancelar)

class CupomModal(ui.Modal):
    def __init__(self, canal_id):
        super().__init__(title=get_text(canal_id, "cupom_modal_titulo"))
        self.canal_id = str(canal_id)
        self.code = ui.TextInput(label=get_text(self.canal_id, "cupom_modal_label"), placeholder="Ex: BORATOPUP1")
        self.add_item(self.code)
        
    async def on_submit(self, interaction: discord.Interaction):
        code = self.code.value.upper()
        if code in CUPONS:
            if self.canal_id not in carrinhos:
                carrinhos[self.canal_id] = {"itens": [], "cupom": None}
            carrinhos[self.canal_id]["cupom"] = code
            save_data()
            await interaction.response.edit_message(embed=criar_embed_resumo(self.canal_id), view=CarrinhoAcoes(self.canal_id))
            
            # Persiste novo estado com cupom aplicado
            orig_msg = await interaction.original_response()
            db.set_internal_data(f"view_state_{self.canal_id}", json.dumps({
                "type": "CarrinhoAcoes",
                "message_id": orig_msg.id,
                "user_id": interaction.user.id
            }))
        else:
            await interaction.response.send_message(get_text(self.canal_id, "cupom_invalido"), ephemeral=True)

class LojaMenu(ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    
    async def check_limit(self, interaction, prefixes):
        user_name = interaction.user.name.lower()
        for thread in interaction.guild.threads:
            if thread.name.startswith(prefixes) and f"-{user_name}" in thread.name.lower() and not thread.archived:
                return True
        return False

    @ui.button(label="🛒 Comprar / Buy", style=discord.ButtonStyle.success, emoji="🛒", custom_id="loja_comprar")
    async def comprar(self, interaction: discord.Interaction, button: ui.Button):
        if await self.check_limit(interaction, ("🛒-", "💰-", "💎-", "💬-")):
            return await interaction.response.send_message("❌ Já possui um ticket aberto!", ephemeral=True)
        try:
            thread = await interaction.channel.create_thread(name=f"🛒-buy-{interaction.user.name.lower()}", auto_archive_duration=60, type=discord.ChannelType.private_thread)
            await thread.add_user(interaction.user)
            await interaction.response.send_message(f"Ticket aberto: {thread.mention}", ephemeral=True)
            v = ui.View()
            async def set_lang(i, lang):
                preferencia_idioma[str(thread.id)] = lang
                # Persistência DB (Integração)
                db.save_service_config(thread.id, interaction.user.id, lang)
                save_data()
                adm_id = IDS_CARGOS_ADM[0] if IDS_CARGOS_ADM else 0
                embed_start = discord.Embed(description=get_text(thread.id, "alerta_staff").format(user=interaction.user.mention, adm=adm_id), color=0x2ecc71)
                embed_jogo = discord.Embed(title=get_text(thread.id, "escolha_jogo_titulo"), description=get_text(thread.id, "escolha_jogo_desc"), color=0x2ecc71)
                embed_jogo.set_image(url=IMAGENS_JOGOS["default"])
                await i.response.edit_message(content=f"<@&{adm_id}>", embeds=[embed_start, embed_jogo], view=EscolhaJogo(thread.id))
                
                # Salva o estado atual
                orig_msg = await i.original_response()
                db.set_internal_data(f"view_state_{thread.id}", json.dumps({
                    "type": "EscolhaJogo",
                    "message_id": orig_msg.id,
                    "user_id": interaction.user.id
                }))
            
            b1 = ui.Button(label="Português", emoji="🇧🇷")
            b1.callback = lambda i: set_lang(i, "pt")
            b2 = ui.Button(label="English", emoji="🇺🇸")
            b2.callback = lambda i: set_lang(i, "en")
            v.add_item(b1).add_item(b2)
            await thread.send(embed=discord.Embed(title="🌐 Language", description="**Selecione o idioma\n Choose your Language:**", color=0x9b59b6), view=v)
        except: pass

    @ui.button(label="💰 Gold / Ouro", style=discord.ButtonStyle.primary, emoji="💰", custom_id="loja_venda_ouro")
    async def venda_ouro(self, interaction: discord.Interaction, button: ui.Button):
        if await self.check_limit(interaction, ("🛒-", "💰-", "💎-", "💬-")):
            return await interaction.response.send_message("❌ Já possui um ticket aberto!", ephemeral=True)
        try:
            thread = await interaction.channel.create_thread(name=f"💰-buy-gold-{interaction.user.name.lower()}", auto_archive_duration=60, type=discord.ChannelType.private_thread)
            await thread.add_user(interaction.user)
            await interaction.response.send_message(f"Ticket Gold: {thread.mention}", ephemeral=True)
            v = ui.View()
            async def set_lang_sell(i, lang):
                preferencia_idioma[str(thread.id)] = lang
                # Persistência DB (Integração)
                db.save_service_config(thread.id, interaction.user.id, lang, 'gold')
                save_data()
                adm_id = IDS_CARGOS_ADM[0] if IDS_CARGOS_ADM else 0
                t = LANGUAGES[lang]
                embed = discord.Embed(title="💰 Venda de Ouro", description=t["venda_ouro_msg"].format(user=interaction.user.mention, adm=adm_id), color=discord.Color.gold())
                if bot.url_banner_gold: embed.set_image(url=bot.url_banner_gold)
                embed.set_footer(text=f"ID: {interaction.user.id}")
                await i.response.edit_message(content=f"<@&{adm_id}>", embed=embed, view=StaffManualConcludeView(thread.id, interaction.user.id))
                
                # Salva o estado atual
                orig_msg = await i.original_response()
                db.set_internal_data(f"view_state_{thread.id}", json.dumps({
                    "type": "StaffManualConcludeView",
                    "message_id": orig_msg.id,
                    "user_id": interaction.user.id
                }))
            
            b1 = ui.Button(label="Português", emoji="🇧🇷")
            b1.callback = lambda i: set_lang_sell(i, "pt")
            b2 = ui.Button(label="English", emoji="🇺🇸")
            b2.callback = lambda i: set_lang_sell(i, "en")
            v.add_item(b1).add_item(b2)
            await thread.send(embed=discord.Embed(title="🌐 Language", description="**Selecione o idioma\n Choose your Language:**", color=0x9b59b6), view=v)
        except: pass

    @ui.button(label="💎 Wemix", style=discord.ButtonStyle.primary, emoji="💎", custom_id="loja_venda_wemix")
    async def venda_wemix(self, interaction: discord.Interaction, button: ui.Button):
        if await self.check_limit(interaction, ("🛒-", "💰-", "💎-", "💬-")):
            return await interaction.response.send_message("❌ Já possui um ticket aberto!", ephemeral=True)
        try:
            thread = await interaction.channel.create_thread(name=f"💎-buy-wemix-{interaction.user.name.lower()}", auto_archive_duration=60, type=discord.ChannelType.private_thread)
            await thread.add_user(interaction.user)
            await interaction.response.send_message(f"Ticket de Venda Wemix: {thread.mention}", ephemeral=True)
            v = ui.View()
            async def set_lang_sell(i, lang):
                preferencia_idioma[str(thread.id)] = lang
                # Persistência DB (Integração)
                db.save_service_config(thread.id, interaction.user.id, lang, 'wemix')
                save_data()
                adm_id = IDS_CARGOS_ADM[0] if IDS_CARGOS_ADM else 0
                t = LANGUAGES[lang]
                embed = discord.Embed(title="💎 Venda de Wemix", description=t["venda_wemix_msg"].format(user=interaction.user.mention, adm=adm_id), color=discord.Color.blue())
                if bot.url_banner_wemix: embed.set_image(url=bot.url_banner_wemix)
                embed.set_footer(text=f"ID: {interaction.user.id}")
                await i.response.edit_message(content=f"<@&{adm_id}>", embed=embed, view=StaffManualConcludeView(thread.id, interaction.user.id))
                
                # Salva o estado atual
                orig_msg = await i.original_response()
                db.set_internal_data(f"view_state_{thread.id}", json.dumps({
                    "type": "StaffManualConcludeView",
                    "message_id": orig_msg.id,
                    "user_id": interaction.user.id
                }))
            
            b1 = ui.Button(label="Português", emoji="🇧🇷")
            b1.callback = lambda i: set_lang_sell(i, "pt")
            b2 = ui.Button(label="English", emoji="🇺🇸")
            b2.callback = lambda i: set_lang_sell(i, "en")
            v.add_item(b1).add_item(b2)
            await thread.send(embed=discord.Embed(title="🌐 Language", description="**Selecione o idioma / Choose your Language:**", color=0x9b59b6), view=v)
        except: pass

    @ui.button(label="💬 Suporte / Support", style=discord.ButtonStyle.secondary, emoji="💬", custom_id="loja_suporte")
    async def suporte(self, interaction: discord.Interaction, button: ui.Button):
        if await self.check_limit(interaction, ("🛒-", "💰-", "💎-", "💬-")):
            return await interaction.response.send_message("❌ Já possui um ticket aberto!", ephemeral=True)
        try:
            thread = await interaction.channel.create_thread(name=f"💬-suporte-{interaction.user.name.lower()}", auto_archive_duration=60, type=discord.ChannelType.private_thread)
            await thread.add_user(interaction.user)
            await interaction.response.send_message(f"Suporte aberto: {thread.mention}", ephemeral=True)
            v = ui.View()
            async def set_lang_support(i, lang):
                preferencia_idioma[str(thread.id)] = lang
                # Persistência DB (Integração)
                db.save_service_config(thread.id, interaction.user.id, lang, 'suporte')
                save_data()
                adm_id = IDS_CARGOS_ADM[0] if IDS_CARGOS_ADM else 0
                t = LANGUAGES[lang]
                embed = discord.Embed(title="💬 Atendimento de Suporte", description=t["suporte_msg"].format(user=interaction.user.mention, adm=adm_id), color=discord.Color.blue())
                embed.set_footer(text=f"ID: {interaction.user.id}")
                await i.response.edit_message(content=f"<@&{adm_id}>", embed=embed, view=StaffManualConcludeView(thread.id, interaction.user.id))
                
                # Salva o estado atual
                orig_msg = await i.original_response()
                db.set_internal_data(f"view_state_{thread.id}", json.dumps({
                    "type": "StaffManualConcludeView",
                    "message_id": orig_msg.id,
                    "user_id": interaction.user.id
                }))
            
            b1 = ui.Button(label="Português", emoji="🇧🇷")
            b1.callback = lambda i: set_lang_support(i, "pt")
            b2 = ui.Button(label="English", emoji="🇺🇸")
            b2.callback = lambda i: set_lang_support(i, "en")
            v.add_item(b1).add_item(b2)
            await thread.send(embed=discord.Embed(title="🌐 Language", description="**Select language for support \n Selecione o idioma para o suporte:**", color=0x9b59b6), view=v)
        except: pass

class LojaMenuPH(ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    
    async def check_limit(self, interaction, prefixes):
        user_name = interaction.user.name.lower()
        for thread in interaction.guild.threads:
            if thread.name.startswith(prefixes) and f"-{user_name}" in thread.name.lower() and not thread.archived:
                return True
        return False

    @ui.button(label="🛒 Buy", style=discord.ButtonStyle.success, emoji="🛒", custom_id="loja_ph_buy")
    async def comprar(self, interaction: discord.Interaction, button: ui.Button):
        if await self.check_limit(interaction, ("🛒-", "💰-", "💎-", "💬-")):
            return await interaction.response.send_message("❌ You already have an active ticket!", ephemeral=True)
        try:
            thread = await interaction.channel.create_thread(name=f"🛒-buy-{interaction.user.name.lower()}", auto_archive_duration=60, type=discord.ChannelType.private_thread)
            await thread.add_user(interaction.user)
            await interaction.response.send_message(f"Ticket opened: {thread.mention}", ephemeral=True)
            preferencia_idioma[str(thread.id)] = "ph"
            # Persistência DB (Integração)
            db.save_service_config(thread.id, interaction.user.id, "ph")
            save_data()
            adm_id = IDS_CARGOS_ADM[0] if IDS_CARGOS_ADM else 0
            embed_start = discord.Embed(description=get_text(thread.id, "alerta_staff").format(user=interaction.user.mention, adm=adm_id), color=0x2ecc71)
            embed_jogo = discord.Embed(title=get_text(thread.id, "escolha_jogo_titulo"), description=get_text(thread.id, "escolha_jogo_desc"), color=0x2ecc71)
            embed_jogo.set_image(url=IMAGENS_JOGOS["default"])
            msg = await thread.send(content=f"<@&{adm_id}>", embeds=[embed_start, embed_jogo], view=EscolhaJogo(thread.id))
            
            # Salva o estado
            db.set_internal_data(f"view_state_{thread.id}", json.dumps({
                "type": "EscolhaJogo",
                "message_id": msg.id,
                "user_id": interaction.user.id
            }))
        except: pass

    @ui.button(label="💰 Buy Gold", style=discord.ButtonStyle.primary, emoji="💰", custom_id="loja_ph_gold")
    async def venda_ouro(self, interaction: discord.Interaction, button: ui.Button):
        if await self.check_limit(interaction, ("🛒-", "💰-", "💎-", "💬-")):
            return await interaction.response.send_message("❌ You already have an active ticket!", ephemeral=True)
        try:
            thread = await interaction.channel.create_thread(name=f"💰-buy-gold-{interaction.user.name.lower()}", auto_archive_duration=60, type=discord.ChannelType.private_thread)
            await thread.add_user(interaction.user)
            await interaction.response.send_message(f"Buy Gold ticket: {thread.mention}", ephemeral=True)
            preferencia_idioma[str(thread.id)] = "ph"
            # Persistência DB (Integração)
            db.save_service_config(thread.id, interaction.user.id, "ph", "gold")
            save_data()
            adm_id = IDS_CARGOS_ADM[0] if IDS_CARGOS_ADM else 0
            t = LANGUAGES["ph"]
            embed = discord.Embed(title="💰 Gold Sell", description=t["venda_ouro_msg"].format(user=interaction.user.mention, adm=adm_id), color=discord.Color.gold())
            if bot.url_banner_gold: embed.set_image(url=bot.url_banner_gold)
            embed.set_footer(text=f"ID: {interaction.user.id}")
            msg = await thread.send(content=f"<@&{adm_id}>", embed=embed, view=StaffManualConcludeView(thread.id, interaction.user.id))
            
            # Salva o estado
            db.set_internal_data(f"view_state_{thread.id}", json.dumps({
                "type": "StaffManualConcludeView",
                "message_id": msg.id,
                "user_id": interaction.user.id
            }))
        except: pass

    @ui.button(label="💎 Wemix Buy", style=discord.ButtonStyle.primary, emoji="💎", custom_id="loja_ph_wemix")
    async def venda_wemix(self, interaction: discord.Interaction, button: ui.Button):
        if await self.check_limit(interaction, ("🛒-", "💰-", "💎-", "💬-")):
            return await interaction.response.send_message("❌ You already have an active ticket!", ephemeral=True)
        try:
            thread = await interaction.channel.create_thread(name=f"💎-buy-wemix-{interaction.user.name.lower()}", auto_archive_duration=60, type=discord.ChannelType.private_thread)
            await thread.add_user(interaction.user)
            await interaction.response.send_message(f"Wemix Buy ticket: {thread.mention}", ephemeral=True)
            preferencia_idioma[str(thread.id)] = "ph"
            # Persistência DB (Integração)
            db.save_service_config(thread.id, interaction.user.id, "ph", "wemix")
            save_data()
            adm_id = IDS_CARGOS_ADM[0] if IDS_CARGOS_ADM else 0
            t = LANGUAGES["ph"]
            embed = discord.Embed(title="💎 Wemix Sell", description=t["venda_wemix_msg"].format(user=interaction.user.mention, adm=adm_id), color=discord.Color.blue())
            if bot.url_banner_wemix: embed.set_image(url=bot.url_banner_wemix)
            embed.set_footer(text=f"ID: {interaction.user.id}")
            msg = await thread.send(content=f"<@&{adm_id}>", embed=embed, view=StaffManualConcludeView(thread.id, interaction.user.id))
            
            # Salva o estado
            db.set_internal_data(f"view_state_{thread.id}", json.dumps({
                "type": "StaffManualConcludeView",
                "message_id": msg.id,
                "user_id": interaction.user.id
            }))
        except: pass

    @ui.button(label="💬 Support", style=discord.ButtonStyle.secondary, emoji="💬", custom_id="loja_ph_support")
    async def suporte(self, interaction: discord.Interaction, button: ui.Button):
        if await self.check_limit(interaction, ("🛒-", "💰-", "💎-", "💬-")):
            return await interaction.response.send_message("❌ You already have an active ticket!", ephemeral=True)
        try:
            thread = await interaction.channel.create_thread(name=f"💬-suporte-{interaction.user.name.lower()}", auto_archive_duration=60, type=discord.ChannelType.private_thread)
            await thread.add_user(interaction.user)
            await interaction.response.send_message(f"Support ticket: {thread.mention}", ephemeral=True)
            preferencia_idioma[str(thread.id)] = "ph"
            # Persistência DB (Integração)
            db.save_service_config(thread.id, interaction.user.id, "ph", "suporte")
            save_data()
            adm_id = IDS_CARGOS_ADM[0] if IDS_CARGOS_ADM else 0
            t = LANGUAGES["ph"]
            embed = discord.Embed(title="💬 Support Ticket", description=t["suporte_msg"].format(user=interaction.user.mention, adm=adm_id), color=discord.Color.blue())
            embed.set_footer(text=f"ID: {interaction.user.id}")
            msg = await thread.send(content=f"<@&{adm_id}>", embed=embed, view=StaffManualConcludeView(thread.id, interaction.user.id))
            
            # Salva o estado
            db.set_internal_data(f"view_state_{thread.id}", json.dumps({
                "type": "StaffManualConcludeView",
                "message_id": msg.id,
                "user_id": interaction.user.id
            }))
        except: pass

@bot.tree.command(name="setup_loja", description="Comando para configurar o menu da loja.")
async def setup_loja(interaction: discord.Interaction):
    if not has_admin_permission(interaction.user):
        return await interaction.response.send_message("❌ Sem Permissão.", ephemeral=True)
    link_prices = f"<#{ID_CANAL_PRICES}>"
    desc = ("Clique no botão **Comprar** abaixo para iniciar sua compra.\n" f"Pode consultar os valores disponíveis no canal {link_prices}\n\n" "---\n" "*Click the **Buy** button below to start your purchase.*\n" f"*Check available prices in {link_prices}*")
    embed = discord.Embed(title="✨ BORA TOP UP STORE", description=desc, color=0xcc2e89)
    embed.set_image(url=URL_BANNER_LOJA)
    await interaction.response.send_message(embed=embed, view=LojaMenu())

@bot.tree.command(name="setup_store_ph", description="I-setup ang store menu para sa Pinoy audience.")
async def setup_loja_ph(interaction: discord.Interaction):
    if not has_admin_permission(interaction.user):
        return await interaction.response.send_message("❌ Sem permissão.", ephemeral=True)
    link_prices = f"<#{ID_CANAL_PHP}>"
    desc = ("Pindutin ang **Buy** button sa ibaba para simulan ang iyong pag bili.\n" f"Maaari mong suriin ang mga available na presyo sa channel {link_prices}\n\n" "---\n" "*Click the **Buy** button below to start your purchase.*")
    embed = discord.Embed(title="✨ BORA TOP UP STORE (PINOY)", description=desc, color=0xcc2e89)
    embed.set_image(url=URL_BANNER_LOJA)
    await interaction.response.send_message(embed=embed, view=LojaMenuPH())

@bot.tree.command(name="force_approve", description="Aprovar pagamento manualmente (Apenas Admin).")
async def force_approve(interaction: discord.Interaction):
    if not has_admin_permission(interaction.user):
        return await interaction.response.send_message("❌ Sem permissão.", ephemeral=True)
    
    if not isinstance(interaction.channel, discord.Thread) or not interaction.channel.name.startswith("🛒-buy-"):
        return await interaction.response.send_message("❌ Só pode usar este comando no canal de compra (🛒)!", ephemeral=True)

    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    
    # --- CORREÇÃO IMPORTANTE: Obter o ID de cliente original do Banco de Dados primeiro para garantir robustez ---
    config = db.get_service_config(interaction.channel_id)
    cliente_id = int(config[2]) if config else None
    
    cliente = None
    if cliente_id:
        try:
            cliente = guild.get_member(cliente_id) or await guild.fetch_member(cliente_id)
        except: pass
        
    if not cliente:
        try:
            cliente_id = interaction.channel.owner_id
            cliente = guild.get_member(cliente_id) or await guild.fetch_member(cliente_id)
        except: pass
    
    if not cliente:
        try:
            thread_members = await interaction.channel.fetch_members()
            for tm in thread_members:
                m = guild.get_member(tm.id) or await guild.fetch_member(tm.id)
                if m and not m.bot and not has_admin_permission(m):
                    cliente = m
                    break
        except: pass

    if not cliente:
        return await interaction.followup.send("❌ Não foi possível identificar o utilizador neste canal.", ephemeral=True)

    for t_existente in guild.threads:
        if "-approved-" in t_existente.name.lower() and f"-{cliente.name.lower()}" in t_existente.name.lower() and not t_existente.archived:
            return await interaction.followup.send(f"❌ User {cliente.mention} already have one ticket opened!", ephemeral=True)

    lang = preferencia_idioma.get(str(interaction.channel_id), "pt")
    bandeira = "🇧🇷" if lang == "pt" else "🇺🇸" if lang == "en" else "🇵🇭"
    
    try:
        dados_carrinho = carrinhos.get(str(interaction.channel_id), {"itens": [], "cupom": None})
        if not dados_carrinho:
            dados_carrinho = {"itens": [], "cupom": None}
            
        # --- NOVIDADE: Buscar itens do banco de dados caso a cache do carrinho em memória esteja vazia ---
        db_items = db.get_cart(interaction.channel_id)
        if db_items and not dados_carrinho.get('itens'):
            dados_carrinho['itens'] = []
            for row in db_items:
                slug, name, qty, price_usdt = row
                dados_carrinho['itens'].append({
                    "nome": name,
                    "nome_slug": slug,
                    "qtd": qty,
                    "total_usdt_item": price_usdt * qty,
                    "emoji": "🪙" if "gold" in slug else "💎"
                })
                
        cupom_utilizado = dados_carrinho.get("cupom") if dados_carrinho else None
        v_usdt = sum(i['total_usdt_item'] for i in dados_carrinho['itens']) if dados_carrinho['itens'] else 0.0
        v_brl = v_usdt * bot.cotacao_dolar
        v_php = v_usdt * bot.cotacao_php
        
        lista_itens_log = ""
        if dados_carrinho and dados_carrinho.get('itens'):
            for it in dados_carrinho['itens']:
                lista_itens_log += f"• {it.get('emoji', '📦')} **{it['nome']}** (x{it['qtd']})\n"

        canal_logs = bot.get_channel(ID_CANAL_LOGS)
        
        # --- GERAÇÃO DO TRANSCRIPT HTML NO MANUAL ---
        transcript_content = await generate_transcript(interaction.channel)

        if canal_logs:
            # DESIGN MELHORADO NO LOG MANUAL
            log_embed = discord.Embed(title="⚠️ APROVAÇÃO MANUAL DE PAGAMENTO" if lang == "pt" else "⚠️ MANUAL PAYMENT APPROVAL" if lang == "en" else "⚠️ MANWAL NA PAG-APRUBA NG BAYAD", color=0xe67e22, timestamp=datetime.datetime.now())
            log_embed.set_thumbnail(url=cliente.display_avatar.url)
            
            log_embed.add_field(name="👤 CLIENTE" if lang == "pt" else "👤 CLIENT" if lang == "en" else "👤 KLIENTE", value=f"{cliente.mention}\n`ID: {cliente.id}`", inline=True)
            log_embed.add_field(name="👮 ADMINISTRADOR" if lang == "pt" else "👮 ADMINISTRATOR" if lang == "en" else "👮 TAGAPANGASIWA", value=f"{interaction.user.mention}", inline=True)
            
            # Mostrar os itens comprados no log manual (Removida a frase genérica de aprovação manual)
            no_items_msg = "Sem itens no carrinho" if lang == "pt" else "No items in cart" if lang == "en" else "Walang mga item sa cart"
            log_embed.add_field(name="📦 PACOTES COMPRADOS" if lang == "pt" else "📦 PACKAGES BOUGHT" if lang == "en" else "📦 MGA BINILING PACK", value=lista_itens_log or f"*{no_items_msg}*", inline=False)
            
            log_embed.add_field(name="🪙 VALOR ESTIMADO" if lang == "pt" else "🪙 ESTIMATED VALUE" if lang == "en" else "🪙 TINATAYANG HALAGA", value=f"**{v_usdt:.2f} USDT**\n(R$ {v_brl:.2f} / {v_php:.2f} PHP)", inline=True)
            log_embed.add_field(name="🎟️ CUPOM" if lang == "pt" else "🎟️ COUPON" if lang == "en" else "🎟️ KUPON", value=f"`{cupom_utilizado or ('NENHUM' if lang == 'pt' else 'NONE' if lang == 'en' else 'WALA')}`", inline=True)
            
            if transcript_content:
                file_obj = io.BytesIO(transcript_content.encode('utf-8'))
                discord_file = discord.File(file_obj, filename=f"transcript_{interaction.channel_id}.html")
                await canal_logs.send(embed=log_embed, file=discord_file)
            else:
                await canal_logs.send(embed=log_embed)

        msg_approved = (
            f"✅ **Pagamento Aprovado por {interaction.user.mention}**" if lang == "pt" 
            else f"✅ **Payment Approved by {interaction.user.mention}**" if lang == "en" 
            else f"✅ **Aprubado ang Bayad ni {interaction.user.mention}**"
        )
        await interaction.channel.send(content=f"{msg_approved}\n{get_text(interaction.channel_id, 'encerrando')}")

        ticket_channel = interaction.channel
        spawn_channel = ticket_channel.parent if isinstance(ticket_channel, discord.Thread) else ticket_channel
        novo_topico = await spawn_channel.create_thread(name=f"{bandeira}-approved-{cliente.name.lower()}", auto_archive_duration=60, type=discord.ChannelType.private_thread)
        
        await novo_topico.add_user(cliente)
        await novo_topico.add_user(interaction.user)
        
        preferencia_idioma[str(novo_topico.id)] = lang
        db.save_service_config(novo_topico.id, cliente.id, lang)

        # PING MANUAL NO FORCED
        adm_id = IDS_CARGOS_ADM[0] if IDS_CARGOS_ADM else 0
        await novo_topico.send(content=f"<@&{adm_id}>")
        
        # --- CORREÇÃO IMPORTANTE: Mostrar o resumo real dos itens comprados no canal de entrega manual ---
        lista_itens_msg = ""
        if dados_carrinho and dados_carrinho.get('itens'):
            lista_itens_msg = "> " + get_text(interaction.channel_id, "resumo_compra_staff") + "\n"
            for it in dados_carrinho['itens']:
                lista_itens_msg += f"> • {it.get('emoji', '📦')} **{it['nome']}** (x{it['qtd']})\n"
        else:
            no_items_msg = "Sem itens no carrinho" if lang == "pt" else "No items in cart" if lang == "en" else "Walang mga item sa cart"
            lista_itens_msg = f"> *{no_items_msg}*"
            
        embed_final = discord.Embed(title=get_text(novo_topico.id, "form_entrega_titulo"), description=get_text(novo_topico.id, "form_entrega_desc").format(itens=lista_itens_msg), color=0x2ecc71)
        embed_final.set_footer(text=f"ID: {cliente.id}")
        
        msg = await novo_topico.send(f"Welcome {cliente.mention}", embed=embed_final, view=AprovadoFormView(novo_topico.id))
        
        # Persiste estado da entrega manual
        db.set_internal_data(f"view_state_{novo_topico.id}", json.dumps({
            "type": "AprovadoFormView",
            "message_id": msg.id,
            "user_id": cliente.id
        }))
        
        # --- ATUALIZAÇÃO DAS ESTATÍSTICAS ---
        await update_sales_stats(cliente.name, v_brl, v_usdt, v_php, cupom_utilizado)
        
        await interaction.followup.send(f"✅ Compra aprovada manualmente! {novo_topico.mention}", ephemeral=True)
        await asyncio.sleep(5)
        await ticket_channel.delete()
        
    except Exception as e:
        await interaction.followup.send(f"❌ Erro ao processar aprovação forçada: {e}", ephemeral=True)

# --- COMANDO SLASH PARA COTAÇÃO DO DÓLAR (MANUAL OU VIA BINANCE) ---
@bot.tree.command(name="set_dolar", description="Define a cotação do dólar (USDT/BRL) manualmente ou automaticamente pela Binance.")
@app_commands.describe(valor="Valor manual do dólar em Reais (ex: 5.50)", usar_binance="Marque como True para buscar o preço real da Binance")
async def set_dolar(interaction: discord.Interaction, valor: float = None, usar_binance: bool = False):
    if not has_admin_permission(interaction.user):
        return await interaction.response.send_message("❌ Sem permissão.", ephemeral=True)
    
    if usar_binance:
        await interaction.response.defer(ephemeral=True)
        try:
            res_brl = requests.get("https://api.binance.com/api/v3/ticker_price?symbol=USDTBRL", timeout=10).json()
            taxa = float(res_brl['price'])
            bot.cotacao_dolar = taxa
            save_data()
            await atualizar_mensagem_precos()
            await interaction.followup.send(f"✅ Cotação do Dólar atualizada automaticamente via Binance: **R$ {taxa:.2f}**", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Erro ao buscar cotação na Binance: {e}", ephemeral=True)
    elif valor is not None:
        bot.cotacao_dolar = valor
        save_data()
        await atualizar_mensagem_precos()
        await interaction.response.send_message(f"✅ Cotação do Dólar definida manualmente para: **R$ {valor:.2f}**", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Erro: Forneça um valor manual ou marque a opção 'usar_binance' como True.", ephemeral=True)

# --- NOVO COMANDO SLASH PARA TABELA DE PREÇOS (image_6c6206_2.png) ---
@bot.tree.command(name="setup_tabela_precos", description="Envia a tabela de preços dinâmica que se atualiza automaticamente.")
async def setup_tabela_precos(interaction: discord.Interaction):
    if not has_admin_permission(interaction.user):
        return await interaction.response.send_message("❌ Sem permissão.", ephemeral=True)
    
    await interaction.response.defer(ephemeral=True)
    
    embed = gerar_embed_tabela_precos()
    # Adicionamos a view persistente de TabelaPrecosView com o painel de alteração interativo
    message = await interaction.channel.send(embed=embed, view=TabelaPrecosView())
    
    # Salva o canal e o ID da mensagem no banco de dados para poder fazer atualizações automáticas
    db.set_internal_data("price_message_channel_id", str(interaction.channel_id))
    db.set_internal_data("price_message_id", str(message.id))
    save_data()
    
    await interaction.followup.send("✅ Tabela de preços enviada com sucesso! Ela irá atualizar automaticamente de forma dinâmica e interativa.", ephemeral=True)

@bot.tree.command(name="feedback_manual", description="Envia o pedido de feedback manualmente para um utilizador via DM.")
@app_commands.describe(user_id="ID do utilizador para enviar o feedback", idioma="Selecione o idioma do atendimento")
@app_commands.choices(idioma=[
    app_commands.Choice(name="Português", value="pt"),
    app_commands.Choice(name="English", value="en"),
    app_commands.Choice(name="Pinoy (PH)", value="ph")
])
async def feedback_manual(interaction: discord.Interaction, user_id: str, idioma: app_commands.Choice[str]):
    if not has_admin_permission(interaction.user):
        return await interaction.response.send_message("❌ Sem permissão.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)
    try:
        # Tenta obter o utilizador pelo ID
        target_user = await bot.fetch_user(int(user_id))
        if not target_user:
            return await interaction.followup.send("❌ Utilizador não encontrado. Verifique o ID.")

        t = LANGUAGES[idioma.value]
        embed = discord.Embed(
            title=t["feedback_dm_title"], 
            description=t["feedback_dm_desc"], 
            color=discord.Color.green()
        )
        
        # Envia a DM com a View que abre o Modal de feedback
        await target_user.send(embed=embed, view=FeedbackDMView(idioma.value))
        
        await interaction.followup.send(f"✅ Pedido de feedback enviado com sucesso para **{target_user.name}** ({user_id}) no idioma **{idioma.name}**.")
    except discord.Forbidden:
        await interaction.followup.send("❌ Não foi possível enviar DM para este utilizador (DMs fechadas).")
    except ValueError:
        await interaction.followup.send("❌ ID inválido. Insira apenas números.")
    except Exception as e:
        await interaction.followup.send(f"❌ Ocorreu um erro: {e}")

@bot.tree.command(name="set_package_brl", description="Define o preço manual em R$ para um pacote.")
@app_commands.describe(pacote="Selecione o pacote", valor="Preço em Reais (Ex: 5.50)")
@app_commands.choices(pacote=[
    app_commands.Choice(name="Pack 1$", value="pack_1"),
    app_commands.Choice(name="Pack 3$", value="pack_3"),
    app_commands.Choice(name="Pack 5$", value="pack_5"),
    app_commands.Choice(name="Pack 10$", value="pack_10"),
    app_commands.Choice(name="Pack 30$", value="pack_30"),
    app_commands.Choice(name="Pack 50$", value="pack_50"),
    app_commands.Choice(name="Pack 100$", value="pack_100")
])
async def set_package_brl(interaction: discord.Interaction, pacote: app_commands.Choice[str], valor: float):
    if not has_admin_permission(interaction.user):
        return await interaction.response.send_message("❌ Sem permissão.", ephemeral=True)
    PRECOS_MANUAIS["pt"][pacote.value] = valor
    save_data()
    await atualizar_mensagem_precos()
    await interaction.response.send_message(f"✅ Preço manual do **{pacote.name}** definido para **R$ {valor:.2f}**.", ephemeral=True)

@bot.tree.command(name="set_package_usdt", description="Define o preço manual em USDT para um pacote.")
@app_commands.describe(pacote="Selecione o pacote", valor="Preço em USDT (Ex: 1.00)")
@app_commands.choices(pacote=[
    app_commands.Choice(name="Pack 1$", value="pack_1"),
    app_commands.Choice(name="Pack 3$", value="pack_3"),
    app_commands.Choice(name="Pack 5$", value="pack_5"),
    app_commands.Choice(name="Pack 10$", value="pack_10"),
    app_commands.Choice(name="Pack 30$", value="pack_30"),
    app_commands.Choice(name="Pack 50$", value="pack_50"),
    app_commands.Choice(name="Pack 100$", value="pack_100")
])
async def set_package_usdt(interaction: discord.Interaction, pacote: app_commands.Choice[str], valor: float):
    if not has_admin_permission(interaction.user):
        return await interaction.response.send_message("❌ Sem permissão.", ephemeral=True)
    PRECOS_MANUAIS["en"][pacote.value] = valor
    save_data()
    await atualizar_mensagem_precos()
    await interaction.response.send_message(f"✅ Preço manual do **{pacote.name}** definido para **{valor:.2f} USDT**.", ephemeral=True)

@bot.tree.command(name="set_package_php", description="Configurar preço manual PHP para um pacote.")
@app_commands.describe(pacote="Escolha o Pacote", valor="Preço (Ex: 56.50)")
@app_commands.choices(pacote=[
    app_commands.Choice(name="Pack 1$", value="pack_1"),
    app_commands.Choice(name="Pack 3$", value="pack_3"),
    app_commands.Choice(name="Pack 5$", value="pack_5"),
    app_commands.Choice(name="Pack 10$", value="pack_10"),
    app_commands.Choice(name="Pack 30$", value="pack_30"),
    app_commands.Choice(name="Pack 50$", value="pack_50"),
    app_commands.Choice(name="Pack 100$", value="pack_100")
])
async def set_package_php(interaction: discord.Interaction, pacote: app_commands.Choice[str], valor: float):
    if not has_admin_permission(interaction.user):
        return await interaction.response.send_message("❌ Sem permissão.", ephemeral=True)
    PRECOS_MANUAIS["ph"][pacote.value] = valor
    save_data()
    await atualizar_mensagem_precos()
    await interaction.response.send_message(f"✅ Preço definido **{pacote.name}** para **{valor:.2f} PHP**.", ephemeral=True)

@bot.tree.command(name="limpar_atendimentos", description="Eliminar todos os chats de compra.")
async def limpar_atendimentos(interaction: discord.Interaction):
    if not has_admin_permission(interaction.user):
        return await interaction.response.send_message("❌ Sem permissão.", ephemeral=True)
    await interaction.response.send_message("🧹 A limpar...", ephemeral=True)
    count = 0
    for thread in interaction.guild.threads:
        if thread.name.startswith(("🛒-",  "💰-", "💎-", "buy-")):
            try:
                await thread.delete()
                count += 1
            except Exception: continue
    await interaction.followup.send(f"✅ `{count}` Tópicos Eliminados.", ephemeral=True)

@bot.tree.command(name="close_gold", description="Fechar Ticket Gold.")
async def close_gold(interaction: discord.Interaction):
    if not has_admin_permission(interaction.user):
        return await interaction.response.send_message(get_text(interaction.channel_id, "erro_perm"), ephemeral=True)
    await interaction.response.send_message(get_text(interaction.channel_id, "encerrando"))
    await asyncio.sleep(6)
    await interaction.channel.delete()

@bot.tree.command(name="close_wemix", description="Fechar Ticket Wemix.")
async def close_wemix(interaction: discord.Interaction):
    if not has_admin_permission(interaction.user):
        return await interaction.response.send_message(get_text(interaction.channel_id, "erro_perm"), ephemeral=True)
    await interaction.response.send_message(get_text(interaction.channel_id, "encerrando"))
    await asyncio.sleep(6)
    await interaction.channel.delete()

@bot.tree.command(name="set_php", description="Alterar preço automático (Pinoy) baseado em P2P.")
@app_commands.describe(valor="Valor de 1 USDT em PHP (ex: 58.20)")
async def set_php(interaction: discord.Interaction, valor: float):
    if not has_admin_permission(interaction.user):
        return await interaction.response.send_message("❌ Sem permissão.", ephemeral=True)
    bot.cotacao_php = valor
    save_data()
    await atualizar_mensagem_precos()
    await interaction.response.send_message(f"✅ Preço PHP P2P atualizado para: **{valor:.2f} PHP/USDT**", ephemeral=True)

@bot.tree.command(name="set_qr_php", description="Configurar QrCode GCASH.")
@app_commands.describe(url="Link direto da imagem do QR Code (ex: https://site.com/imagem.png)")
async def set_qr_php(interaction: discord.Interaction, url: str):
    if not has_admin_permission(interaction.user):
        return await interaction.response.send_message("❌ Sem permissão.", ephemeral=True)
    bot.url_qr_php = url
    save_data()
    await interaction.response.send_message(f"✅ QR Code PHP atualizado com sucesso!", ephemeral=True)

@bot.tree.command(name="set_qr_binance", description="Configurar o link do Binance QR Code.")
@app_commands.describe(url="Link direto da imagem do QR Code (ex: https://site.com/imagem.png)")
async def set_qr_binance(interaction: discord.Interaction, url: str):
    if not has_admin_permission(interaction.user):
        return await interaction.response.send_message("❌ Sem permissão.", ephemeral=True)
    bot.url_qr_binance = url
    save_data()
    await interaction.response.send_message(f"✅ QR Code atualizado com sucesso!", ephemeral=True)

@bot.tree.command(name="set_banner_gold", description="Configurar o banner para os tickets Sell Gold.")
@app_commands.describe(url="Link direto da imagem (ex: https://site.com/image.png)")
async def set_banner_gold(interaction: discord.Interaction, url: str):
    if not has_admin_permission(interaction.user):
        return await interaction.response.send_message("❌ Sem permissão.", ephemeral=True)
    bot.url_banner_gold = url
    save_data()
    await interaction.response.send_message(f"✅ Banner Gold atualizado com sucesso!", ephemeral=True)

@bot.tree.command(name="set_banner_wemix", description="Configurar o banner para os tickets Sell Wemix.")
@app_commands.describe(url="Link direto da imagem (ex: https://site.com/image.png)")
async def set_banner_wemix(interaction: discord.Interaction, url: str):
    if not has_admin_permission(interaction.user):
        return await interaction.response.send_message("❌ Sem permissão.", ephemeral=True)
    bot.url_banner_wemix = url
    save_data()
    await interaction.response.send_message(f"✅ Banner Wemix updated!", ephemeral=True)

@bot.tree.command(name="sent_custom_embed", description="Enviar um embed personalizado.")
@app_commands.describe(titulo="Título do embed", descricao="Descrição/texto (use \\n para saltar linhas)", imagem_url="Link para imagem grande (opcional)", thumbnail_url="Link para ícone pequeno (opcional)")
async def enviar_custom_embed(interaction: discord.Interaction, titulo: str, descricao: str, imagem_url: str = None, thumbnail_url: str = None):
    if not has_admin_permission(interaction.user):
        return await interaction.response.send_message("❌ Sem permissão.", ephemeral=True)
    descricao = descricao.replace("\\n", "\n")
    embed = discord.Embed(title=titulo, description=descricao, color=0x3498db)
    if imagem_url: embed.set_image(url=imagem_url)
    if thumbnail_url: embed.set_thumbnail(url=thumbnail_url)
    await interaction.channel.send(embed=embed)
    await interaction.response.send_message("✅ Embed enviado!", ephemeral=True)

@bot.tree.command(name="stats_vendas", description="Mostrar relatório detalhado de vendas.")
async def stats_vendas(interaction: discord.Interaction):
    if not has_admin_permission(interaction.user):
        return await interaction.response.send_message("❌ Sem permissão.", ephemeral=True)
    
    embed = discord.Embed(title="📊 Painel de Vendas", color=0x3498db)
    vendas = ticket_stats['vendas_concluidas']
    t_brl = ticket_stats['valor_total_brl']
    t_usdt = ticket_stats['valor_total_usdt']
    t_php = ticket_stats.get('valor_total_php', 0.0)
    
    embed.add_field(name="✅ Total Vendas", value=f"`{vendas}`", inline=True)
    embed.add_field(name="💰 Total BRL", value=f"`R$ {t_brl:.2f}`", inline=True)
    embed.add_field(name="🪙 Total USDT", value=f"`{t_usdt:.2f} USDT`", inline=True)
    embed.add_field(name="🇵🇭 Total PHP", value=f"`{t_php:.2f} PHP`", inline=True)
    
    # Exibir contagem de uso de cupões
    cupons_txt = "\n".join([f"🏷️ `{cp}`: {qtd}" for cp, qtd in ticket_stats["uso_cupons"].items()]) or "Sem cupões usados ainda."
    embed.add_field(name="🎟️ Uso de Cupões", value=cupons_txt, inline=False)
    
    if ticket_stats["historico"]:
        hist_txt = ""
        # Limita aos últimos 10 para evitar erro de 1024 caracteres
        for item in ticket_stats["historico"][:10]:
            cupom = item.get("cupom", "Nenhum")
            linha = f"📅 `{item['data']}` | 👤 **{item['cliente']}** (🎟️ `{cupom}`)\n└ 💰 `R$ {item['brl']:.2f}` | `{item.get('php', 0.0):.2f} PHP` ({item['usdt']:.2f} USDT)\n"
            
            # Verificação extra de segurança para não quebrar o campo
            if len(hist_txt) + len(linha) < 1000:
                hist_txt += linha
            else:
                break
                
        embed.add_field(name="📦 Histórico (Últimos 10)", value=hist_txt or "Histórico vazio.", inline=False)
    else: 
        embed.add_field(name="📦 Histórico", value="Sem tickets registados no histórico ainda.", inline=False)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="reset_stats", description="Reiniciar TODAS as estatísticas de vendas (Apenas Admin).")
async def reset_stats(interaction: discord.Interaction):
    if not has_admin_permission(interaction.user):
        return await interaction.response.send_message("❌ Sem permissão.", ephemeral=True)
    
    ticket_stats["vendas_concluidas"] = 0
    ticket_stats["valor_total_brl"] = 0.0
    ticket_stats["valor_total_usdt"] = 0.0
    ticket_stats["valor_total_php"] = 0.0
    ticket_stats["uso_cupons"] = {}
    ticket_stats["historico"] = []
    
    save_data()
    await interaction.response.send_message("✅ **Estatísticas reiniciadas com sucesso!**", ephemeral=True)
    

if __name__ == "__main__":
    load_data()
    bot.run(TOKEN)
