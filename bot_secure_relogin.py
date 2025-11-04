from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
# ⚠️ Removido: from webdriver_manager.chrome import ChromeDriverManager 
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
    """Retorna a cor conforme o multiplicador."""
    m = float(value)
    if 1.0 <= m < 2.0:
        return "blue-bg"
    if 2.0 <= m < 10.0:
        return "purple-bg"
    if m >= 10.0:
        return "magenta-bg"
    return "default-bg"

def safe_click(driver, by, value, timeout=5):
    """Tenta clicar em um elemento de forma segura."""
    try:
        el = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((by, value)))
        el.click()
        return True
    except Exception:
        return False

def safe_find(driver, by, value, timeout=5):
    """Tenta encontrar um elemento de forma segura."""
    try:
        return WebDriverWait(driver, timeout).until(EC.presence_of_element_located((by, value)))
    except Exception:
        return None


def initialize_game_elements(driver):
    """Localiza iframe e histórico do Aviator."""
    POSSIVEIS_IFRAMES = [
        '//iframe[contains(@src, "/aviator/")]',
        '//iframe[contains(@src, "spribe")]',
        '//iframe[contains(@src, "aviator-game")]'
    ]
    
    # LISTA DE SELETORES EXPANDIDA E MAIS ROBUSTA
    POSSIVEIS_HISTORICOS = [
        # 🆕 NOVO SELETOR ADICIONADO PARA MAIOR ROBUSTEZ
        ('.round-history-button-1-x', By.CSS_SELECTOR),
        
        # Seletores CSS (mais comuns)
        ('.rounds-history', By.CSS_SELECTOR),
        ('.history-list', By.CSS_SELECTOR),
        ('.multipliers-history', By.CSS_SELECTOR),
        ('.result-history', By.CSS_SELECTOR),
        ('[data-testid="history"]', By.CSS_SELECTOR),
        ('.game-history', By.CSS_SELECTOR),
        # Seletores de fallback
        ('.bet-history', By.CSS_SELECTOR),
        ('div[class*="recent-list"]', By.CSS_SELECTOR),
        ('ul.results-list', By.CSS_SELECTOR),
        ('div.history-block', By.CSS_SELECTOR),
        ('div[class*="history-container"]', By.CSS_SELECTOR),
        # Seletores XPath genéricos (última tentativa)
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

    historico_elemento = None
    for selector, by_method in POSSIVEIS_HISTORICOS:
        try:
            historico_elemento = WebDriverWait(driver, 7).until(
                EC.presence_of_element_located((by_method, selector))
            )
            print(f"✅ Seletor de histórico encontrado: {selector} ({by_method})")
            break
        except Exception:
            continue

    if not historico_elemento:
        print("⚠️ Nenhum seletor de histórico encontrado! O bot pode congelar.")
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
        
        # 4. Clica no botão final de login
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
    
    # ⬆️ TEMPO DE ESPERA AUMENTADO para garantir o carregamento do histórico
    sleep(15) 
    
    return True

def start_driver():
    """
    Inicializa o driver do Chrome.
    ⚠️ CORRIGIDO: Adaptado para a Square Cloud, usando o ChromeDriver instalado via APT
    """
    options = webdriver.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-blink-features=AutomationControlled")
    # NECESSÁRIO para servidores sem interface gráfica
    options.add_argument("--headless") 
    options.add_argument("--window-size=1920,1080")
    
    # ⚠️ Aponta para o local onde o `chromium-chromedriver` é instalado pelo APT (square.json)
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
        # Chama a si mesma para tentar novamente do zero em caso de falha inicial
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

            # === REINÍCIO PROGRAMADO DIÁRIO (23:59 BR) ===
            # Verifica se é 23:59 (ou maior) e se o reinício ainda não foi feito hoje
            if now_br.hour == 23 and now_br.minute >= 59 and (relogin_done_for != now_br.date()):
                print(f"🕛 REINÍCIO PROGRAMADO: Fechando bot às {now_br.strftime('%H:%M:%S')} para reabrir após 00:00.")
                driver.quit()
                
                # O BOT FICARÁ OFFLINE POR 60 SEGUNDOS
                print("💤 Bot offline por 1 minuto... (Reiniciando em 00:00:xx)")
                sleep(60) 
                
                # Reinicia o script, atualizando o dia para evitar repetição
                return start_bot(relogin_done_for=now_br.date()) 
            # =========================================

            # === VERIFICAÇÃO DE INATIVIDADE (6 MIN) ===
            if (time() - ULTIMO_MULTIPLIER_TIME) > TEMPO_MAX_INATIVIDADE:
                 print(f"🚨 Inatividade por mais de 6 minutos! Último envio em: {datetime.fromtimestamp(ULTIMO_MULTIPLIER_TIME).strftime('%H:%M:%S')}. Reiniciando o bot...")
                 driver.quit()
                 # Reinicia o script do zero
                 return start_bot()
            # =========================================


            # === GARANTE QUE ESTAMOS NO IFRAME ANTES DE LER ===
            try:
                # O switch_to.frame deve ocorrer antes de acessar hist
                driver.switch_to.frame(iframe) 
            except Exception:
                # Se falhar, tenta restabelecer o iframe e hist
                driver.switch_to.default_content()
                iframe, hist = initialize_game_elements(driver) 
                if not hist:
                    print("⚠️ Falha crítica: Iframe/Histórico perdido. Reiniciando o bot...")
                    driver.quit()
                    return start_bot() 

            # === LEITURA DOS RESULTADOS ===
            resultados_texto = hist.text.strip() if hist else ""
            if not resultados_texto:
                falhas += 1
                if falhas > 5:
                    print("⚠️ Mais de 5 falhas de leitura. Tentando re-inicializar elementos...")
                    driver.switch_to.default_content()
                    iframe, hist = initialize_game_elements(driver)
                    falhas = 0
                sleep(1)
                continue
            
            falhas = 0 # Se leu com sucesso, zera as falhas

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

            # === ENVIO PARA FIREBASE ===
            if resultados:
                novo = resultados[0] 
                if (novo != LAST_SENT) and ((time() - ULTIMO_ENVIO) > INTERVALO_MINIMO_ENVIO):
                    
                    now = datetime.now()
                    now_br = now.astimezone(TZ_BR)

                    raw = f"{novo:.2f}"
                    date_str = now_br.strftime("%Y-%m-%d")
                    time_key = now_br.strftime("%H-%M-%S.%f")
                    time_display = now_br.strftime("%H:%M:%S")
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
                    ULTIMO_MULTIPLIER_TIME = time() # Reseta o timer de inatividade
            
            # ⚠️ LINHA REMOVIDA/COMENTADA: driver.switch_to.default_content()
            # Mantenha o foco no iframe para leituras rápidas
            sleep(POLLING_INTERVAL)

        except (StaleElementReferenceException, TimeoutException):
            print("⚠️ Elemento histórico obsoleto/sumiu. Recarregando elementos...")
            # Não precisa de switch_to.default_content() aqui, pois já será feito em initialize_game_elements
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
        # Chama a função inicial com o dia atual para controle do reinício
        start_bot(relogin_done_for=date.today())
