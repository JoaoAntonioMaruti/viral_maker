(() => {
  const TIMEOUT_MS = 60_000;
  const POLL_INTERVAL_MS = 100;
  const CLOSE_DELAY_MS = 1_000;
  const DIALOG_READY_DELAY_MS = 2_000;

  const sleep = (milliseconds) =>
    new Promise((resolve) => setTimeout(resolve, milliseconds));

  const waitFor = async (condition, description) => {
    const startedAt = Date.now();
    while (Date.now() - startedAt < TIMEOUT_MS) {
      const result = condition();
      if (result) {
        return result;
      }
      await sleep(POLL_INTERVAL_MS);
    }
    throw new Error(`${description} was not available after ${TIMEOUT_MS}ms`);
  };

  const waitForWindowEvent = (eventName) =>
    new Promise((resolve, reject) => {
      const handleEvent = (event) => {
        clearTimeout(timeoutId);
        resolve(event);
      };
      const timeoutId = setTimeout(() => {
        window.removeEventListener(eventName, handleEvent);
        reject(new Error(`${eventName} was not emitted after ${TIMEOUT_MS}ms`));
      }, TIMEOUT_MS);
      window.addEventListener(eventName, handleEvent, { once: true });
    });

  const isVisible = (element) => {
    if (!element || !element.isConnected) {
      return false;
    }
    const style = window.getComputedStyle(element);
    const bounds = element.getBoundingClientRect();
    return (
      style.display !== "none" &&
      style.visibility !== "hidden" &&
      Number.parseFloat(style.opacity) !== 0 &&
      bounds.width > 0 &&
      bounds.height > 0
    );
  };

  const waitForUnityReady = async () => {
    await waitFor(() => window.unityInstance, "window.unityInstance");
    const loadingOverlay = document.querySelector("#unity-logo-overlay");
    if (loadingOverlay) {
      await waitFor(
        () => !isVisible(loadingOverlay),
        "Unity loading overlay to disappear",
      );
    }
  };

  const waitForVisibleElement = (selector) =>
    waitFor(() => {
      const element = document.querySelector(selector);
      return isVisible(element) ? element : null;
    }, `Visible element ${selector}`);

  const waitForClickableButton = (selector) =>
    waitFor(() => {
      const button = document.querySelector(selector);
      const style = button ? window.getComputedStyle(button) : null;
      const isEnabled =
        button &&
        !button.disabled &&
        button.getAttribute("aria-disabled") !== "true" &&
        style?.pointerEvents !== "none";
      return isVisible(button) && isEnabled ? button : null;
    }, `Clickable button ${selector}`);

  const disableAnimations = () => {
    const style = document.createElement("style");
    style.dataset.screenshotStyles = "true";
    style.textContent = `
      *, *::before, *::after {
        caret-color: transparent !important;
        scroll-behavior: auto !important;
        transition: none !important;
      }
    `;
    document.head.appendChild(style);
  };

  const acceptLegalAge = async (modal) => {
    const legalAgeSwitch = modal.querySelector("#legal-age-switch");
    if (!legalAgeSwitch) {
      throw new Error("Element #legal-age-switch was not found");
    }
    if (!legalAgeSwitch.checked) {
      const visibleSwitch = await waitForVisibleElement(
        'label[for="legal-age-switch"]',
      );
      visibleSwitch.click();
    }
    await waitFor(
      () => legalAgeSwitch.checked,
      "Checked element #legal-age-switch",
    );
  };

  const closeFirstModal = async (modal) => {
    const closeButton = await waitForClickableButton("#close-first-modal-button");
    await sleep(CLOSE_DELAY_MS);
    closeButton.click();
    await waitFor(() => !isVisible(modal), "First modal to close");
  };

  const handleFirstModal = async () => {
    const modal = document.querySelector("#first-run-modal");
    if (!isVisible(modal)) {
      return;
    }
    await acceptLegalAge(modal);
    await closeFirstModal(modal);
  };

  const injectScreenshotAuth = async () => {
    await waitFor(
      () => typeof storage !== "undefined" && typeof storage.setItem === "function",
      "storage.setItem",
    );
    storage.setItem("authToken", "screenshot-fake-auth-token");
    if (!storage.getItem("userId")) {
      storage.setItem("userId", "screenshot-fake-user");
    }
  };

  const openNpcDialog = async () => {
    const npcId = window.__screenshotConfig?.npcId;
    if (typeof npcId !== "string" || !npcId.trim()) {
      throw new Error("window.__screenshotNpcId is required");
    }
    await waitFor(
      () => typeof window._setClosedNpc === "function",
      "Function _setClosedNpc",
    );
    await waitFor(
      () => typeof window.openDialogAreainJavaScriptLayer === "function",
      "Function openDialogAreainJavaScriptLayer",
    );
    await waitFor(
      () => typeof store !== "undefined" && typeof store.setState === "function",
      "store.setState",
    );
    store.setState("npcLocation", "none");
    await Promise.resolve(window._setClosedNpc(npcId));
    await Promise.resolve(window.openDialogAreainJavaScriptLayer());
  };

  const renderNpcClothes = async () => {
    const clothes = window.__screenshotConfig?.clothes;
    if (typeof clothes !== "string" || !clothes.trim()) {
      throw new Error("window.__screenshotConfig.clothes is required");
    }
    await waitFor(
      () => typeof window._internalRendererNpc === "function",
      "Function _internalRendererNpc",
    );
    await Promise.resolve(window._internalRendererNpc(clothes, null));
  };

  const mockChatAndWaitForTyping = async () => {
    await sleep(DIALOG_READY_DELAY_MS);
    const chatTextBox = await waitFor(
      () =>
        typeof window.chatTextBoxV2?.mock === "function"
          ? window.chatTextBoxV2
          : null,
      "window.chatTextBoxV2.mock",
    );
    const mockData = window.__screenshotConfig?.mock;
    if (
      !mockData ||
      typeof mockData.npcName !== "string" ||
      typeof mockData.description !== "string" ||
      typeof mockData.message !== "string" ||
      !Array.isArray(mockData.actions)
    ) {
      throw new Error("window.__screenshotConfig.mock is invalid");
    }
    const typingFinished = waitForWindowEvent("chatTextBoxTypingFinished");
    chatTextBox.mock(mockData);
    await typingFinished;
    await waitFor(
      () => {
        const visibleActions = Array.from(
          document.querySelectorAll(
            '[data-recordable-action="chat-action"].action-visible',
          ),
        ).filter(isVisible);
        return mockData.actions.every((action) =>
          visibleActions.some(
            (element) => element.textContent.trim() === action,
          ),
        );
      },
      "All mocked chat actions to become visible",
    );
  };

  const prepareScreenshot = async () => {
    await waitForUnityReady();
    await handleFirstModal();
    await injectScreenshotAuth();
    await openNpcDialog();
    await renderNpcClothes();
    await mockChatAndWaitForTyping();
    disableAnimations();
    window.scrollTo(0, 0);
    document.body.dataset.screenshotReady = "true";
  };

  return prepareScreenshot();
})();
