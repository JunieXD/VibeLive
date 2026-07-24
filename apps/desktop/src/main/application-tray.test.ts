import { beforeEach, describe, expect, it, vi } from "vitest";

const electronMocks = vi.hoisted(() => {
  const trayInstances: Array<{
    icon: unknown;
    listeners: Map<string, () => void>;
    contextMenu?: unknown;
    toolTip: string;
  }> = [];
  const buildFromTemplate = vi.fn((template: Array<{ id?: string }>) => ({
    getMenuItemById: (id: string) => template.find((item) => item.id === id)
  }));

  class MockTray {
    readonly listeners = new Map<string, () => void>();
    contextMenu: unknown;
    toolTip = "";

    constructor(readonly icon: unknown) {
      trayInstances.push(this);
    }

    on(event: string, listener: () => void): this {
      this.listeners.set(event, listener);
      return this;
    }

    setContextMenu(menu: unknown): void {
      this.contextMenu = menu;
    }

    setToolTip(toolTip: string): void {
      this.toolTip = toolTip;
    }
  }

  return { buildFromTemplate, MockTray, trayInstances };
});

vi.mock("electron", () => ({
  Menu: { buildFromTemplate: electronMocks.buildFromTemplate },
  Tray: electronMocks.MockTray
}));

import { createApplicationTray, TRAY_MENU_ITEM_IDS } from "./application-tray";

describe("application tray", () => {
  beforeEach(() => {
    electronMocks.buildFromTemplate.mockClear();
    electronMocks.trayInstances.length = 0;
  });

  it("keeps the running app visible and exposes open and full-exit actions", () => {
    const showControlWindow = vi.fn();
    const quitApplication = vi.fn();
    const icon = { isEmpty: () => false };
    const applicationTray = createApplicationTray({
      icon: icon as never,
      showControlWindow,
      quitApplication
    });

    expect(electronMocks.trayInstances).toHaveLength(1);
    expect(electronMocks.trayInstances[0].icon).toBe(icon);
    expect(electronMocks.trayInstances[0].toolTip).toBe("ADVX Live - 正在运行");
    expect(electronMocks.trayInstances[0].contextMenu).toBe(applicationTray.menu);

    electronMocks.trayInstances[0].listeners.get("click")?.();
    expect(showControlWindow).toHaveBeenCalledOnce();

    const openItem = applicationTray.menu.getMenuItemById(TRAY_MENU_ITEM_IDS.open);
    const quitItem = applicationTray.menu.getMenuItemById(TRAY_MENU_ITEM_IDS.quit);
    expect(openItem?.label).toBe("打开 ADVX Live");
    expect(quitItem?.label).toBe("彻底退出");

    openItem?.click?.(openItem, undefined, {} as never);
    quitItem?.click?.(quitItem, undefined, {} as never);
    expect(showControlWindow).toHaveBeenCalledTimes(2);
    expect(quitApplication).toHaveBeenCalledOnce();
  });
});
