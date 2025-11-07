#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Versão otimizada do bot. Objetivos:
 - localizar iframe e histórico muito mais rápido
 - evitar sleeps longos
 - usar execute_script para checks rápidos
 - injetar MutationObserver no iframe para expor histórico em window._aviatorHistory
 - reduzir polling e overhead Selenium
Substitua seu arquivo inteiro por este.
"""

import time
import json
import logging
from typing import Tuple, Optional, List

from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, StaleElementReferenceException, WebDriverException, NoSuchElementException
)

# CONFIGS - ajuste conforme necessário
CHROME_DRIVER_PATH = "/usr/bin/chromedriver"  # ajuste se for diferente
HEADLESS = False
POLLING_INTERVAL = 0.3          # leitura do history (reduza se CPU aguentar)
IFRAME_LOOKUP_QUICK_TIMEOUT = 7
HISTORY_LOOKUP_QUICK_TIMEOUT = 5
MUTATION_OBSERVER_INJECTION = True
LOG_LEVEL = logging.INFO

# Logging minimalista
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bot_opt")

def create_driver() -> webdriver.Chrome:
    options = Options()
    if HEADLESS:
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1280,800")

    # Performance options: bloqueia imagens e reduz recursos
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-gpu")
    # diminui carga de recursos (images disabled)
    options.add_argument("--blink-settings=imagesEnabled=false")
    # evita muitos logs do chrome
    options.add_experimental_option("excludeSwitches", ["enable-logging", "enable-automation"])
    # preferências para não carregar fonts desnecessárias (economia pequena)
    prefs = {
        "profile.managed_default_content_settings.images": 2,
        "profile.managed_default_content_settings.fonts": 2
    }
    options.add_experimental_option("prefs", prefs)

    service = ChromeService(CHROME_DRIVER_PATH)
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(40)
    return driver

# --- Busca turbo de iframe e histórico ---
POSSIVEIS_IFRAMES_SUBSTR = ["aviator", "spribe", "aviator-game", "game-iframe", "widget"]
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

def quick_find_iframe(driver) -> Optional[webdriver.remote.webelement.WebElement]:
    """Busca iframes existentes e verifica src rapidamente sem esperar longos timeouts."""
    try:
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
    except WebDriverException:
        iframes = []

    for f in iframes:
        try:
            src = (f.get_attribute("src") or "").lower()
        except Exception:
            src = ""
        for sub in POSSIVEIS_IFRAMES_SUBSTR:
            if sub in src:
                log.debug("iframe encontrado pelo src contendo: %s", sub)
                return f

    # fallback por xpath curto (rápido)
    for sub in POSSIVEIS_IFRAMES_SUBSTR:
        try:
            els = driver.find_elements(By.XPATH, f'//iframe[contains(@src, "{sub}")]')
            if els:
                log.debug("iframe encontrado por xpath contendo: %s", sub)
                return els[0]
        except Exception:
            continue

    return None

def initialize_game_elements(driver) -> Tuple[Optional[webdriver.remote.webelement.WebElement], Optional[webdriver.remote.webelement.WebElement]]:
    """
    Localiza e retorna (iframe_element, history_element).
    Procedimento:
     - busca rápida sem espera longa
     - se não encontrado, espera curta (IFRAME_LOOKUP_QUICK_TIMEOUT)
     - troca para iframe e busca history com execute_script + find_elements
     - se não encontrado, tenta espera curta por seletores conhecidos
    """
    log.info("Inicializando elementos do jogo (turbo).")
    # 1) busca rápida
    iframe = quick_find_iframe(driver)
    if not iframe:
        log.info("iframe não encontrado na varredura inicial. Tentando espera curta (%ss).", IFRAME_LOOKUP_QUICK_TIMEOUT)
        try:
            iframe = WebDriverWait(driver, IFRAME_LOOKUP_QUICK_TIMEOUT).until(
                EC.presence_of_element_located((By.XPATH, '//iframe[contains(@src, "aviator") or contains(@src, "spribe") or contains(@src, "aviator-game")]'))
            )
        except TimeoutException:
            log.warning("iframe não apareceu dentro do timeout curto.")
            return None, None

    # 2) switch para iframe e busca do histórico por queries rápidas
    try:
        driver.switch_to.default_content()
        driver.switch_to.frame(iframe)
    except Exception as e:
        log.warning("Falha ao trocar para iframe: %s", e)
        return None, None

    historico = None

    # 2a) tenta querySelector via execute_script para checar existência rapidamente
    for selector, by_method in POSSIVEIS_HISTORICOS:
        try:
            if by_method == By.CSS_SELECTOR:
                # escape simples para selectors que contenham aspas
                js_selector = selector.replace("'", "\\'")
                js = f"try{{return !!document.querySelector('{js_selector}');}}catch(e){{return false;}}"
                exists = driver.execute_script(js)
                if exists:
                    # transforma para WebElement real
                    found = driver.find_elements(By.CSS_SELECTOR, selector)
                    if found:
                        historico = found[0]
                        log.info("Histórico encontrado rapidamente via selector: %s", selector)
                        break
            else:
                found = driver.find_elements(By.XPATH, selector)
                if found:
                    historico = found[0]
                    log.info("Histórico encontrado rapidamente via xpath: %s", selector)
                    break
        except Exception:
            continue

    # 2b) espera curta se não encontrado
    if not historico:
        log.info("Histórico não encontrado com buscas instantâneas. Tentando espera curta (%ss).", HISTORY_LOOKUP_QUICK_TIMEOUT)
        for selector, by_method in POSSIVEIS_HISTORICOS:
            try:
                historico = WebDriverWait(driver, HISTORY_LOOKUP_QUICK_TIMEOUT).until(
                    EC.presence_of_element_located((by_method, selector))
                )
                log.info("Histórico encontrado com wait: %s", selector)
                break
            except TimeoutException:
                continue
            except Exception:
                continue

    if not historico:
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        log.error("Nenhum seletor de histórico detectado.")
        return iframe, None

    return iframe, historico

# --- MutationObserver injection ---
MUTATION_SCRIPT = """
(function(){
  if (window._aviatorHistoryInstalled) return true;
  window._aviatorHistoryInstalled = true;
  window._aviatorHistory = window._aviatorHistory || [];
  function pushIfNew(val){
    try{
      if(!val) return;
      var last = window._aviatorHistory[0];
      if(String(last) !== String(val)){
         window._aviatorHistory.unshift(val);
         if(window._aviatorHistory.length>200) window._aviatorHistory.length=200;
      }
    }catch(e){}
  }
  // observer on common containers
  var targets = document.querySelectorAll('ul, div, .history-list, .rounds-list, .results-list');
  targets.forEach(function(t){
    try{
      var mo = new MutationObserver(function(muts){
        muts.forEach(function(m){
          if(m.addedNodes && m.addedNodes.length){
            m.addedNodes.forEach(function(n){
              try{
                var text = '';
                if(n.innerText) text = n.innerText.trim();
                if(!text && n.textContent) text = n.textContent.trim();
                if(text){
                  // try extract multiplier-like token (e.g., "x1.23" or "1.23")
                  var mchs = text.match(/\\d+(?:[\\.,]\\d+)?/g);
                  if(mchs && mchs.length){
                    pushIfNew(mchs[0]);
                  } else {
                    pushIfNew(text);
                  }
                }
              }catch(e){}
            });
          }
        });
      });
      mo.observe(t, {childList:true, subtree:true});
    }catch(e){}
  });
  // initial population: try to read existing items
  try{
    var existing = [];
    var els = document.querySelectorAll('li, div, .history-list li, .rounds-list li, .results-list li');
    els.forEach(function(e){ if(e && e.innerText) existing.push(e.innerText.trim()); });
    for(var i=0;i<existing.length;i++){
      var v = existing[i];
      if(v) window._aviatorHistory.push(v);
    }
    window._aviatorHistory.reverse();
    window._aviatorHistory = window._aviatorHistory.slice(0,200);
  }catch(e){}
  return true;
})();
"""

def inject_mutation_observer_if_needed(driver) -> bool:
    if not MUTATION_OBSERVER_INJECTION:
        return False
    try:
        injected = driver.execute_script("return !!window._aviatorHistoryInstalled;")
        if injected:
            return True
    except Exception:
        # execute may fail if page not fully ready; ignore and try injecting
        pass
    try:
        driver.execute_script(MUTATION_SCRIPT)
        log.info("MutationObserver injetado no iframe (ou já estava presente).")
        return True
    except Exception as e:
        log.warning("Falha ao injetar MutationObserver: %s", e)
        return False

def read_history_via_window(driver, max_items=50) -> List[str]:
    """Lê history exposto via window._aviatorHistory. Retorna lista de strings (mais recente primeiro)."""
    try:
        data = driver.execute_script("return (window._aviatorHistory || []).slice(0, arguments[0]);", max_items)
        if not data:
            return []
        # normaliza para strings
        return [str(x).strip() for x in data if x is not None]
    except Exception:
        return []

def read_history_via_dom(historico_element, max_items=50) -> List[str]:
    """Fallback: lê historico via parsing do elemento (innerText split)."""
    try:
        txt = historico_element.get_attribute("innerText") or historico_element.text or ""
        lines = [l.strip() for l in txt.splitlines() if l.strip()]
        # últimos primeiros se o site listar cronologicamente
        lines = lines[:max_items]
        return lines
    except StaleElementReferenceException:
        return []
    except Exception:
        return []

# --- Exemplo de loop principal de captura de velas ---
def capture_loop(driver, iframe_element, historico_element):
    """
    Loop principal que captura velas.
    Assume driver já está no contexto certo (ou re-enters quando necessário).
    """
    log.info("Iniciando loop de captura com polling %.2fs", POLLING_INTERVAL)
    last_seen = None
    try:
        # já estamos no frame; garantimos observer
        inject_mutation_observer_if_needed(driver)
    except Exception:
        pass

    while True:
        try:
            # sempre tenta ler via array JS primeiro - mais rápido
            history = read_history_via_window(driver, max_items=30)
            if not history:
                # fallback DOM
                history = read_history_via_dom(historico_element, max_items=30)

            if history:
                most_recent = history[0]
                if most_recent != last_seen:
                    last_seen = most_recent
                    # aqui sua lógica de processamento das velas
                    log.info("Novo item detectado: %s", most_recent)
                    # EXEMPLO: parse para float quando possível
                    try:
                        val = float(str(most_recent).replace(',', '.'))
                        # processa val conforme sua estratégia
                    except Exception:
                        pass
            # dinâmica de polling
            time.sleep(POLLING_INTERVAL)
        except KeyboardInterrupt:
            log.info("Loop interrompido pelo usuário.")
            break
        except StaleElementReferenceException:
            log.warning("Historico stale. Re-resolvendo elementos.")
            # tentamos re-inicializar elementos rápido
            try:
                driver.switch_to.default_content()
                driver.switch_to.frame(iframe_element)
                # atualiza referencia historico
                # tenta encontrar novamente sem esperar muito
                for selector, by in POSSIVEIS_HISTORICOS:
                    try:
                        if by == By.CSS_SELECTOR:
                            found = driver.find_elements(By.CSS_SELECTOR, selector)
                        else:
                            found = driver.find_elements(By.XPATH, selector)
                        if found:
                            historico_element = found[0]
                            break
                    except Exception:
                        continue
                inject_mutation_observer_if_needed(driver)
            except Exception:
                log.exception("Erro re-resolvendo após stale.")
                break
        except WebDriverException as e:
            log.error("WebDriverException no loop: %s", e)
            break
        except Exception as e:
            log.exception("Erro inesperado no loop: %s", e)
            # tentativa curta de recovery
            time.sleep(1)
            continue

def process_login_and_start(driver, start_url: str):
    """
    Abre a página, faz login (se necessário) e tenta iniciar captura rapidamente.
    Esta função faz poucas suposições sobre o fluxo de login.
    Substitua partes específicas de login por suas rotinas se precisar.
    """
    log.info("Abrindo URL: %s", start_url)
    driver.get(start_url)

    # espera curta para o iframe do jogo aparecer — não bloqueia muito
    try:
        WebDriverWait(driver, 12).until(
            EC.presence_of_element_located((By.XPATH, '//iframe[contains(@src, "aviator") or contains(@src, "spribe") or contains(@src,"aviator-game")]'))
        )
    except TimeoutException:
        log.info("iframe não apareceu dentro de 12s. Tentando localizar rapidamente sem bloquear.")

    # inicializa elementos do jogo
    iframe_el, hist_el = initialize_game_elements(driver)
    if not iframe_el:
        log.error("Falha: iframe não detectado. Encerrando tentativa.")
        return False

    if not hist_el:
        log.warning("Histórico não encontrado inicialmente. Ainda assim entrando no iframe e injetando observer.")
        try:
            driver.switch_to.default_content()
            driver.switch_to.frame(iframe_el)
            inject_mutation_observer_if_needed(driver)
            # tenta localizar historico outra vez de forma não-blocking
            for selector, by in POSSIVEIS_HISTORICOS:
                try:
                    if by == By.CSS_SELECTOR:
                        found = driver.find_elements(By.CSS_SELECTOR, selector)
                    else:
                        found = driver.find_elements(By.XPATH, selector)
                    if found:
                        hist_el = found[0]
                        break
                except Exception:
                    continue
        except Exception:
            log.exception("Erro ao entrar no iframe após não encontrar histórico.")
            return False

    # switch para frame final e inicia loop
    try:
        driver.switch_to.default_content()
        driver.switch_to.frame(iframe_el)
    except Exception:
        log.exception("Erro ao trocar para iframe antes do loop.")
        return False

    capture_loop(driver, iframe_el, hist_el)
    return True

def main():
    # Exemplo de uso. Substitua start_url pelo site que você usa.
    start_url = "https://www.exemplo-com-jogo.com"  # << substitua aqui
    driver = create_driver()
    try:
        ok = process_login_and_start(driver, start_url)
        if not ok:
            log.error("Processo de inicialização falhou.")
    finally:
        try:
            driver.quit()
        except Exception:
            pass

if __name__ == "__main__":
    main()
