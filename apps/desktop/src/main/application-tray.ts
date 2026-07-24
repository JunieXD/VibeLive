import { Menu, Tray, type MenuItemConstructorOptions, type NativeImage } from "electron";

export const TRAY_MENU_ITEM_IDS = {
  open: "open-advx-live",
  quit: "quit-advx-live"
} as const;

type ApplicationTrayOptions = {
  icon: NativeImage;
  showControlWindow: () => void;
  quitApplication: () => void;
};

export type ApplicationTray = {
  menu: Menu;
  tray: Tray;
};

export function createApplicationTray(options: ApplicationTrayOptions): ApplicationTray {
  const template: MenuItemConstructorOptions[] = [
    {
      id: TRAY_MENU_ITEM_IDS.open,
      label: "打开 ADVX Live",
      click: options.showControlWindow
    },
    { type: "separator" },
    {
      id: TRAY_MENU_ITEM_IDS.quit,
      label: "彻底退出",
      click: options.quitApplication
    }
  ];
  const menu = Menu.buildFromTemplate(template);
  const tray = new Tray(options.icon);
  tray.setToolTip("ADVX Live - 正在运行");
  tray.setContextMenu(menu);
  tray.on("click", options.showControlWindow);
  return { menu, tray };
}
