from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from time import sleep, time
from datetime import datetime, date
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
import firebase_admin
from firebase_admin import credentials
from firebase_admin import db
import os
import pytz 

# =============================================================
# 🔥 CONFIGURAÇÃO FIREBASE
# =============================================================
SERVICE_ACCOUNT_FILE = 'serviceAccountKey.json'
DATABASE_URL = 'https://history-dashboard-a70ee-default-rtdb.firebaseio.com'

try:
    if not firebase_admin._apps:
        cred = credentials.Certificate(SERVICE_ACCOUNT_FILE)
        firebase_admin.initialize_app(cred, {
            'databaseURL': DATABASE_URL
        })
    print("✅ Firebase Admin SDK inicializado com sucesso. O bot salvará dados.")
except FileNotFoundError:
    print("\n❌ ERRO CRÍTICO: Arquivo de credenciais 'serviceAccountKey.json' não encontrado.")
    print("Baixe a chave JSON do console do Firebase e coloque na mesma pasta deste script.")
    exit()
except Exception as e:
    print(f"\n❌ ERRO DE CONEXÃO FIREBASE: {e}")
    exit()

# =============================================================
# ⚙️ VARIÁVEIS PRINCIPAIS
# =============================================================
URL_DO_SITE = "https://www.goathbet.com"
LINK_AVIATOR = "https://www.goathbet.com/game/spribe-aviator"
COOKIES_FILE = "cookies.pkl" 

EMAIL = os.getenv("EMAIL")
PASSWORD = os.getenv("PASSWORD")

POLLING_INTERVAL = 1.0          # Intervalo entre as checagens (1 segundo)
INTERVALO_MINIMO_ENVIO = 2.0    # Mínimo de tempo entre dois envios (segundos)
TEMPO_MAX_INATIVIDADE = 360     # 6 minutos (360 segundos)
TZ_BR = pytz.timezone("America/Sao_Paulo")

# =============================================================
# 🔧 FUNÇÕES AUXILIARES
# =============================================================
def getColorClass(value):
    m = float(value)
    if 1.0 <= m < 2.0:
        return "blue-bg"
    if 2.0 <= m < 10.0:
        return "purple-bg"
    if m >= 10.0:
        return "magenta-bg"
    return "default-bg"

def safe_click(driver, by, value, timeout=5):
    try:
        el = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((by, value)))
        el.click()
        return True
    except Exception:
        return False

def safe_find(driver, by, value, timeout=5):
    try:
        return WebDriverWait(driver, timeout).until(EC.presence_of_element_located((by, value)))
    except Exception:
        return None

# =============================================================
# 🔎 LOCALIZA IFRAME + HISTÓRICO (com retry incremental)
# =============================================================
def initialize_game_elements(driver):
    """Localiza iframe e histórico do Aviator de forma robusta para ambiente cloud."""
    POSSIVEIS_IFRAMES = [
        '//iframe[contains(@src, "/aviator/")]',
        '//iframe[contains(@src, "spribe")]',
        '//iframe[contains(@src, "aviator-game")]'
    ]
    # prioriza o seletor que você confirmou nos logs
    POSSIVEIS_HISTORICOS = [
        ('.result-history', By.CSS_SELECTOR),
        ('.round-history-button-1-x', By.CSS_SELECTOR),
        ('.rounds-history', By.CSS_SELECTOR),
        ('.history-list', By.CSS_SELECTOR),
        ('.multipliers-history', By.CSS_SELECTOR),
        ('[data-testid="history"]', By.CSS_SELECTOR),
        ('.game-history', By.CSS_SELECTOR),
        ('.bet-history', By.CSS_SELECTOR),
        ('div[class*="recent-list"]', By.CSS_SELECTOR),
        ('ul.results-list', By.CSS_SELECTOR),
        ('div.history-block', By.CSS_SELECTOR),
        ('div[class*="history-container"]', By.CSS_SELECTOR),
        ('//div[contains(@class, "history")]', By.XPATH),
        ('//div[contains(@class, "rounds-list")]', By.XPATH)
    ]

    iframe = None
    for xpath in POSSIVEIS_IFRAMES:
        try:
            driver.switch_to.default_content()
            iframe = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, xpath))
            )
            driver.switch_to.frame(iframe)
            print(f"✅ Iframe encontrado com XPath: {xpath}")
            break
        except Exception:
            continue

    if not iframe:
        print("⚠️ Nenhum iframe encontrado. Verifique se o jogo está carregado.")
        return None, None

    # 1) Tentativa rápida: até 5s
    historico_elemento = None
    for selector, by_method in POSSIVEIS_HISTORICOS:
        try:
            historico_elemento = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((by_method, selector))
            )
            print(f"✅ Seletor de histórico encontrado: {selector} ({by_method})")
            break
        except Exception:
            continue

    # 2) Cloud é mais lenta: retry incremental até 60s sem travar
    if not historico_elemento:
        print("ℹ️ Histórico não visível ainda. Ativando busca incremental de até 60s...")
        deadline = time() + 60
        while time() < deadline and not historico_elemento:
            for selector, by_method in POSSIVEIS_HISTORICOS:
                try:
                    if by_method == By.CSS_SELECTOR:
                        found = driver.find_elements(By.CSS_SELECTOR, selector)
                    else:
                        found = driver.find_elements(By.XPATH, selector)
                    if found:
                        historico_elemento = found[0]
                        print(f"✅ Histórico detectado com busca incremental: {selector}")
                        break
                except Exception:
                    continue
            if historico_elemento:
                break
            sleep(0.5)

    if not historico_elemento:
        print("❌ Nenhum seletor de histórico encontrado após 60s.")
        driver.switch_to.default_content()
        return None, None

    return iframe, historico_elemento

# =============================================================
# 🔑 FLUXO DE LOGIN E NAVEGAÇÃO
# =============================================================
def process_login(driver):
    """Executa o fluxo de login e navegação para o Aviator."""
    if not EMAIL or not PASSWORD:
        print("❌ ERRO: EMAIL ou PASSWORD não configurados.")
        return False

    print("➡️ Executando login automático...")

    driver.get(URL_DO_SITE)
    sleep(2)

    # 1. Confirma maior de 18
    if safe_click(driver, By.CSS_SELECTOR, 'button[data-age-action="yes"]', 5):
        print("✅ Confirmado maior de 18.")
        sleep(1)

    # 2. Abre janela de login
    if not safe_click(driver, By.CSS_SELECTOR, 'a[data-ix="window-login"].btn-small.w-button', 5):
        print("❌ Botão 'Login' inicial não encontrado.")
        return False
    sleep(1)

    # 3. Preenche e-mail e senha
    email_input = safe_find(driver, By.ID, "field-15", 5)
    pass_input = safe_find(driver, By.ID, "password-login", 5)

    if email_input and pass_input:
        email_input.clear()
        email_input.send_keys(EMAIL)
        pass_input.clear()
        pass_input.send_keys(PASSWORD)
        sleep(0.5)
        if safe_click(driver, By.CSS_SELECTOR, "a[login-btn].btn-small.btn-color-2.full-width.w-inline-block", 5):
            print("✅ Credenciais preenchidas e login confirmado.")
            sleep(5)
        else:
            print("❌ Botão final de login não encontrado ou falha ao clicar.")
            return False
    else:
        print("⚠️ Campos de login não encontrados!")
        return False

    # 5. Aceita cookies
    safe_click(driver, By.XPATH, "//button[contains(., 'Aceitar')]", 4)
    print("✅ Cookies aceitos (se aplicável).")
    sleep(1)

    # 6. Abre Aviator
    if safe_click(driver, By.CSS_SELECTOR, "img.slot-game", 4):
        print("✅ Aviator aberto via imagem.")
    else:
        driver.get(LINK_AVIATOR)
        print("ℹ️ Indo direto para o Aviator via link.")

    # Em cloud manter uma janela de respiro para o provider preparar o iframe
    sleep(12)
    return True

# =============================================================
# 🧭 DRIVER (headless cloud) + micro otimizações
# =============================================================
def start_driver():
    """
    Inicializa o driver do Chrome para Square Cloud (headless).
    """
    options = webdriver.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--headless")
    options.add_argument("--window-size=1920,1080")
    # menos carga: imagens off
    options.add_argument("--blink-settings=imagesEnabled=false")

    # Caminho padrão do Chromium/ChromeDriver na Square Cloud
    service = Service("/usr/lib/chromium-browser/chromedriver")

    return webdriver.Chrome(service=service, options=options)

# =============================================================
# 🚀 LOOP PRINCIPAL
# =============================================================
def start_bot(relogin_done_for: date = None):
    print("\n==============================================")
    print("         INICIALIZANDO GOATHBOT")
    print("==============================================")

    driver = start_driver()

    # === FLUXO DE INICIALIZAÇÃO E RECONEXÃO ===
    def setup_game(driver):
        if not process_login(driver):
            return None, None
        iframe, hist = initialize_game_elements(driver)
        if not hist:
            print("❌ Não conseguiu iniciar o jogo. Tentando novamente...")
            return None, None
        return iframe, hist

    iframe, hist = setup_game(driver)

    if not hist:
        driver.quit()
        return start_bot()

    LAST_SENT = None
    ULTIMO_ENVIO = time()
    ULTIMO_MULTIPLIER_TIME = time()
    falhas = 0
    relogin_done_for = relogin_done_for if relogin_done_for else date.today()

    print("✅ Captura iniciada.\n")

    while True:
        try:
            now_br = datetime.now(TZ_BR)

            # Reinício diário às 23:59 BR
            if now_br.hour == 23 and now_br.minute >= 59 and (relogin_done_for != now_br.date()):
                print(f"🕛 REINÍCIO PROGRAMADO: Fechando bot às {now_br.strftime('%H:%M:%S')} para reabrir após 00:00.")
                driver.quit()
                print("💤 Bot offline por 1 minuto... (Reiniciando em 00:00:xx)")
                sleep(60)
                return start_bot(relogin_done_for=now_br.date())

            # Inatividade > 6 min
            if (time() - ULTIMO_MULTIPLIER_TIME) > TEMPO_MAX_INATIVIDADE:
                print(f"🚨 Inatividade > 6min. Reiniciando...")
                driver.quit()
                return start_bot()

            # Garante contexto no iframe
            try:
                driver.switch_to.frame(iframe)
            except Exception:
                driver.switch_to.default_content()
                iframe, hist = initialize_game_elements(driver)
                if not hist:
                    print("⚠️ Iframe/Histórico perdido. Reiniciando o bot...")
                    driver.quit()
                    return start_bot()

            # Leitura dos resultados
            resultados_texto = hist.text.strip() if hist else ""
            if not resultados_texto:
                falhas += 1
                if falhas > 5:
                    print("⚠️ 5 falhas seguidas. Re-inicializando elementos...")
                    driver.switch_to.default_content()
                    iframe, hist = initialize_game_elements(driver)
                    falhas = 0
                sleep(1)
                continue

            falhas = 0

            resultados = []
            seen = set()
            for n in resultados_texto.split("\n"):
                n = n.replace("x", "").strip()
                try:
                    if n:
                        v = float(n)
                        if v >= 1.0 and v not in seen:
                            seen.add(v)
                            resultados.append(v)
                except ValueError:
                    pass

            # Envio para Firebase
            if resultados:
                novo = resultados[0]
                if (novo != LAST_SENT) and ((time() - ULTIMO_ENVIO) > INTERVALO_MINIMO_ENVIO):
                    now = datetime.now().astimezone(TZ_BR)
                    raw = f"{novo:.2f}"
                    date_str = now.strftime("%Y-%m-%d")
                    time_key = now.strftime("%H-%M-%S.%f")
                    time_display = now.strftime("%H:%M:%S")
                    color = getColorClass(novo)

                    entry_key = f"{date_str}_{time_key}_{raw}x".replace(':', '-').replace('.', '-')
                    entry = {"multiplier": raw, "time": time_display, "color": color, "date": date_str}

                    try:
                        db.reference(f"history/{entry_key}").set(entry)
                        print(f"🔥 {raw}x salvo às {time_display}")
                    except Exception as e:
                        print("⚠️ Erro ao salvar:", e)

                    LAST_SENT = novo
                    ULTIMO_ENVIO = time()
                    ULTIMO_MULTIPLIER_TIME = time()

            # Mantém foco no iframe durante polling
            sleep(POLLING_INTERVAL)

        except (StaleElementReferenceException, TimeoutException):
            print("⚠️ Elemento histórico obsoleto/sumiu. Recarregando elementos...")
            iframe, hist = initialize_game_elements(driver)
            continue
        except Exception as e:
            print(f"❌ Erro inesperado: {e}")
            sleep(3)
            continue

# =============================================================
# ▶️ INÍCIO DO SCRIPT
# =============================================================
if __name__ == "__main__":
    if not EMAIL or not PASSWORD:
        print("\n❗ Configure as variáveis de ambiente EMAIL e PASSWORD ou defina-as diretamente no código.")
    else:
        start_bot(relogin_done_for=date.today())
