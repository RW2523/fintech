/** The icons the workbench uses, drawn inline.
 *
 *  Inline rather than from a package: the published app is served under a
 *  content-security policy that allows no external asset, and a font or sprite
 *  sheet that silently fails to load leaves a screen of empty boxes. These are
 *  a few hundred bytes and cannot fail.
 *
 *  Every one is decorative. Each sits beside a label that says the same thing,
 *  so they carry `aria-hidden` and a screen reader reads the words. */

type Props = { className?: string };

function Svg({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className ?? "h-4 w-4"}
    >
      {children}
    </svg>
  );
}

export const Icon = {
  dashboard: (p: Props) => (
    <Svg {...p}>
      <path d="M3 10.5 12 3l9 7.5" />
      <path d="M5 9.5V20h14V9.5" />
      <path d="M9.5 20v-5.5h5V20" />
    </Svg>
  ),
  applications: (p: Props) => (
    <Svg {...p}>
      <path d="M6 3h8l4 4v14H6z" />
      <path d="M14 3v4h4" />
      <path d="M9 12h6M9 16h6" />
    </Svg>
  ),
  documents: (p: Props) => (
    <Svg {...p}>
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
    </Svg>
  ),
  collections: (p: Props) => (
    <Svg {...p}>
      <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />
    </Svg>
  ),
  members: (p: Props) => (
    <Svg {...p}>
      <circle cx="9" cy="8" r="3.2" />
      <path d="M3 19c0-3 2.7-5 6-5s6 2 6 5" />
      <path d="M16 5.5a3.2 3.2 0 0 1 0 6M17.5 14c2.2.5 3.5 2.2 3.5 5" />
    </Svg>
  ),
  sandbox: (p: Props) => (
    <Svg {...p}>
      <path d="M12 3 4 6.5V12c0 5 3.4 8 8 9 4.6-1 8-4 8-9V6.5z" />
      <path d="m9 12 2 2 4-4" />
    </Svg>
  ),
  ledger: (p: Props) => (
    <Svg {...p}>
      <path d="M6 3h12v18l-6-3-6 3z" />
      <path d="M9 8h6M9 12h6" />
    </Svg>
  ),
  settings: (p: Props) => (
    <Svg {...p}>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2v3M12 19v3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M2 12h3M19 12h3M4.9 19.1 7 17M17 7l2.1-2.1" />
    </Svg>
  ),

  /* --- meaning --------------------------------------------------------- */
  spark: (p: Props) => (
    <Svg {...p}>
      <path d="M12 3.5 13.8 9l5.7 1.8-5.7 1.8L12 18.5 10.2 12.6 4.5 10.8 10.2 9z" />
    </Svg>
  ),
  check: (p: Props) => (
    <Svg {...p}>
      <circle cx="12" cy="12" r="9" />
      <path d="m8.5 12 2.4 2.4 4.6-4.8" />
    </Svg>
  ),
  alert: (p: Props) => (
    <Svg {...p}>
      <path d="M12 4.5 21 19.5H3z" />
      <path d="M12 10v4M12 17h.01" />
    </Svg>
  ),
  info: (p: Props) => (
    <Svg {...p}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v5M12 8h.01" />
    </Svg>
  ),
  clock: (p: Props) => (
    <Svg {...p}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5.2l3.2 1.9" />
    </Svg>
  ),
  chart: (p: Props) => (
    <Svg {...p}>
      <path d="M5 19V11M12 19V5M19 19v-5" />
    </Svg>
  ),
  scale: (p: Props) => (
    <Svg {...p}>
      <path d="M12 4v16M7 20h10" />
      <path d="M5 8h14M5 8l-2.5 5h5zM19 8l-2.5 5h5z" />
    </Svg>
  ),
  chat: (p: Props) => (
    <Svg {...p}>
      <path d="M20 15a3 3 0 0 1-3 3H9l-4 3v-3.5A3 3 0 0 1 4 15V7a3 3 0 0 1 3-3h10a3 3 0 0 1 3 3z" />
    </Svg>
  ),
  route: (p: Props) => (
    <Svg {...p}>
      <circle cx="6" cy="6" r="2.5" />
      <circle cx="18" cy="18" r="2.5" />
      <path d="M8.5 6H14a4 4 0 0 1 0 8H9a4 4 0 0 0 0 8h.5" />
    </Svg>
  ),
  shield: (p: Props) => (
    <Svg {...p}>
      <path d="M12 3 4 6.5V12c0 5 3.4 8 8 9 4.6-1 8-4 8-9V6.5z" />
    </Svg>
  ),
  arrowRight: (p: Props) => (
    <Svg {...p}>
      <path d="M5 12h13M13 6.5 18.5 12 13 17.5" />
    </Svg>
  ),
  up: (p: Props) => (
    <Svg {...p}>
      <path d="M12 19V6M6.5 11.5 12 6l5.5 5.5" />
    </Svg>
  ),
  down: (p: Props) => (
    <Svg {...p}>
      <path d="M12 5v13M6.5 12.5 12 18l5.5-5.5" />
    </Svg>
  ),
  file: (p: Props) => (
    <Svg {...p}>
      <path d="M7 3h7l4 4v14H7z" />
      <path d="M14 3v4h4" />
    </Svg>
  ),
  search: (p: Props) => (
    <Svg {...p}>
      <circle cx="11" cy="11" r="6.5" />
      <path d="m16 16 4.5 4.5" />
    </Svg>
  ),
  signOut: (p: Props) => (
    <Svg {...p}>
      <path d="M14 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4" />
      <path d="M10 8 6 12l4 4M6 12h9" />
    </Svg>
  ),
};

export type IconName = keyof typeof Icon;
