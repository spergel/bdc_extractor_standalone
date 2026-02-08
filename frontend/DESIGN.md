# Aesthetic Design Document
## Windows 95 / Excel 90s Theme

## 🎨 Color Palette

### Primary Colors (Windows 95 Style)
- **Background**: `#c0c0c0` - Classic Windows 95 gray
- **Panel Background**: `#ffffff` - White for panels/windows
- **Text Primary**: `#000000` - Black text
- **Text Secondary**: `#808080` - Gray text for secondary
- **Selected/Active**: `#000080` - Windows blue (selected items)
- **Highlight**: `#316ac5` - Bright blue for links/active

### Button Colors
- **Button Face**: `#c0c0c0` - Standard button gray
- **Button Highlight**: `#ffffff` - Top/left border (raised effect)
- **Button Shadow**: `#808080` - Bottom/right border (shadow)
- **Button Dark Shadow**: `#000000` - Darker shadow for depth
- **Button Pressed**: Inverted - highlight becomes shadow

### Semantic Colors
- **Success/OK**: `#00ff00` - Classic green
- **Warning**: `#ffff00` - Yellow
- **Danger**: `#ff0000` - Red
- **Border Light**: `#ffffff` - Light border (top/left)
- **Border Dark**: `#808080` - Dark border (bottom/right)

### Excel-Specific Colors
- **Grid Lines**: `#c0c0c0` - Light gray grid
- **Header Background**: `#c0c0c0` - Gray header
- **Selected Cell**: `#316ac5` - Blue selection
- **Chart Colors**: `#0000ff`, `#ff0000`, `#00ff00`, `#ffff00`, `#ff00ff`, `#00ffff` - Classic Excel colors

## 📐 Typography

### Font Family
- **Primary**: System font (MS Sans Serif aesthetic)
  - Windows: `"MS Sans Serif"` or `"Segoe UI"`
  - Mac: `"Helvetica"` or system default
  - Fallback: `sans-serif`
- **Usage**: System fonts for Windows 95 authentic feel
- **Monospace**: Only for data/code (numbers, tables when appropriate)

### Font Sizes
- **Large**: `text-lg` (1.125rem) - Headers, important numbers
- **Base**: `text-sm` (0.875rem) - Primary body text
- **Small**: `text-xs` (0.75rem) - Secondary info, labels
- **Micro**: `text-[11px]` or `text-[8px]` - Chart labels, fine print

### Font Weights
- **Normal**: Default weight for most text
- **Medium**: `font-medium` - Headers, emphasized labels
- **Semibold**: `font-semibold` - Badges, important labels

## 🎭 Component Styling

### Windows/Panels
```css
.window {
  background: #ffffff;
  border: 2px outset #c0c0c0;
  /* Windows 95 raised border effect */
  border-top: 2px solid #ffffff;
  border-left: 2px solid #ffffff;
  border-right: 2px solid #808080;
  border-bottom: 2px solid #808080;
  /* No border-radius - sharp corners */
}
```

### Title Bars
```css
.titlebar {
  padding: 0.25rem 0.5rem;
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #000080; /* Windows blue */
  color: #ffffff;
  font-weight: bold;
  border-bottom: 2px solid #000000;
  /* Sharp corners */
}
```

### Buttons (Windows 95 Style)
```css
.btn {
  background: #c0c0c0;
  padding: 0.125rem 0.75rem;
  border: 2px outset #c0c0c0;
  /* Raised effect */
  border-top: 2px solid #ffffff;
  border-left: 2px solid #ffffff;
  border-right: 2px solid #808080;
  border-bottom: 2px solid #808080;
  color: #000000;
  font-size: 11px;
  cursor: pointer;
  /* Sharp corners */
}

.btn:hover {
  border-style: outset;
  border-color: #c0c0c0;
}

.btn:active {
  border: 2px inset #c0c0c0;
  /* Pressed effect - inverted */
  border-top: 2px solid #808080;
  border-left: 2px solid #808080;
  border-right: 2px solid #ffffff;
  border-bottom: 2px solid #ffffff;
}

.btn-primary {
  background: #c0c0c0;
  /* Same beveled style */
}
```

### Inputs (Windows 95 Style)
```css
.input {
  background: #ffffff;
  padding: 0.125rem 0.25rem;
  border: 2px inset #c0c0c0;
  /* Inset effect */
  border-top: 2px solid #808080;
  border-left: 2px solid #808080;
  border-right: 2px solid #ffffff;
  border-bottom: 2px solid #ffffff;
  color: #000000;
  font-size: 11px;
  /* Sharp corners */
}

.input:focus {
  outline: 1px dotted #000000;
  outline-offset: -3px;
}
```

### Badges
```css
.badge {
  font-size: 0.75rem;
  padding: 0.125rem 0.5rem;
  border-radius: 0.25rem;
  border: 1px solid;
  font-weight: 600;
}

.badge-ok {
  background: rgba(15, 77, 74, 0.2);
  color: #0f4d4a;
  border-color: rgba(15, 77, 74, 0.5);
}

.badge-warn {
  background: rgba(216, 163, 26, 0.2);
  color: #d8a31a;
  border-color: rgba(216, 163, 26, 0.5);
}

.badge-danger {
  background: rgba(194, 58, 43, 0.2);
  color: #c23a2b;
  border-color: rgba(194, 58, 43, 0.5);
}
```

## 🎯 Interactive Elements

### Tabs (Windows 95 Style)
- **Active Tab**: 
  - Text: `#000000` (black)
  - Background: `#ffffff` (white)
  - Border: `2px outset #c0c0c0` (raised)
  - Bottom border: Merges with content area
  
- **Inactive Tab**:
  - Text: `#000000` (black)
  - Background: `#c0c0c0` (gray)
  - Border: `2px outset #c0c0c0` (raised)
  - Hover: Slight background change

### Hover States
- **Table Rows**: `#c0c0c0` background (gray highlight)
- **Buttons**: Border stays raised, slight color change
- **Charts**: Points change color slightly on hover
- **Links**: `#0000ff` (blue) underline

### Loading States
- **Spinner**: Simple rotating element with Windows 95 colors
- **Overlay**: `#c0c0c0/80` (gray with opacity) with spinner
- **Text**: "Loading..." or "Sorting..." in `#000000` (black)

## 📊 Charts & Visualizations

### Chart Styling (Excel 95 Style)
- **Line Colors**: Classic Excel colors - `#0000ff` (blue), `#ff0000` (red), `#00ff00` (green), `#ffff00` (yellow), `#ff00ff` (magenta), `#00ffff` (cyan)
- **Point Color**: `#0000ff` (blue), `#316ac5` on hover
- **Area Fill**: Solid colors with low opacity, no gradients
- **Axes**: `#000000` (black) - Sharp, chunky lines
- **Grid Lines**: `#c0c0c0` (light gray) - Subtle grid
- **Axis Labels**: `#000000`, `text-[11px]` - Black text
- **Tooltip**: 
  - Background: `#ffffff` (white)
  - Border: `2px inset #c0c0c0` (beveled border)
  - Text: `#000000` (black)

### Chart Grid
- Responsive SVG with `viewBox` for scaling
- Maintain aspect ratio with `aspectRatio` CSS
- Use `preserveAspectRatio="xMidYMid meet"` for proper scaling

## 🎨 Visual Effects

### Beveled Borders (Windows 95 Style)
- **Raised/Outset**: 
  - Top/Left: `#ffffff` (white)
  - Bottom/Right: `#808080` (gray)
  - Creates raised 3D effect
  
- **Sunken/Inset**:
  - Top/Left: `#808080` (gray)
  - Bottom/Right: `#ffffff` (white)
  - Creates pressed/inset effect

- **Standard Border**: `2px solid #c0c0c0` (flat gray)

### Shadows
- **No modern shadows** - Use beveled borders instead
- **No glows** - Windows 95 didn't have glow effects
- **Depth via borders** - 3D effect comes from light/dark border sides

### Animations
- **Minimal transitions** - Keep very short (0.1s) or none
- **No smooth easing** - Instant or linear transitions only
- **Button press** - Instant border inversion (outset → inset)
- **Spinner**: Simple rotating element, no fancy effects

## 📐 Layout & Spacing

### Grid System
- **Main Layout**: `grid grid-cols-[16rem_1fr]` (sidebar + main content)
- **Tab Content**: `grid grid-cols-1 md:grid-cols-2` (responsive charts)
- **Gap**: `gap-4` (1rem) between major sections, `gap-2` (0.5rem) for tight spacing

### Padding
- **Panels**: `p-3` (0.75rem) standard
- **Compact**: `p-2` (0.5rem) for tight spaces
- **Spacious**: `p-4` (1rem) for important content

### Border Radius
- **ALL ELEMENTS**: `border-radius: 0` - **SHARP CORNERS ONLY**
- No rounded corners anywhere - this is Windows 95/Excel 90s aesthetic

## 🔤 Text Colors & Hierarchy

### Text Color Hierarchy
1. **Primary**: `#000000` (black) - Main content on white/gray backgrounds
2. **Secondary**: `#808080` (gray) - Labels, secondary info
3. **Disabled**: `#c0c0c0` (light gray) - Disabled text
4. **Links**: `#0000ff` (blue) - Hyperlinks
5. **Selected**: `#ffffff` (white) - Text on Windows blue background
6. **Highlight**: `#316ac5` (bright blue) - Active/selected items

### Windows 95 Text Rules
- Black text on white/gray backgrounds
- White text on Windows blue (#000080) backgrounds
- Gray text for disabled/secondary elements
- Blue text for links and active states

## 🎯 Consistency Rules

1. **Sharp corners everywhere** - No border-radius (border-radius: 0)
2. **Windows blue (#000080) for selected/active** - Use sparingly for emphasis
3. **Light backgrounds** - White/gray backgrounds, not dark
4. **Beveled borders** - Use 2px borders with light/dark sides for 3D effect
5. **Minimal transitions** - Keep animations simple and quick
6. **Compact spacing** - Windows 95 was tight, use smaller padding
7. **Grid alignment** - Everything should align to a grid
8. **System fonts** - Use system fonts for authentic Windows 95 feel
9. **Classic gray palette** - Stick to #c0c0c0, #ffffff, #808080, #000000
10. **Chunky borders** - 2px borders throughout, not thin 1px

## 🚫 Anti-Patterns (Don't Do This)

### Design Anti-Patterns
- ❌ Rounded corners (Windows 95 had sharp corners!)
- ❌ Modern shadows/blurs (use beveled borders instead)
- ❌ Smooth gradients (use flat colors)
- ❌ Modern animations (keep transitions minimal)
- ❌ Sans-serif fonts (use system fonts like MS Sans Serif aesthetic)
- ❌ Multiple accent colors (stick to Windows blue #000080)
- ❌ Glassmorphism/frosted effects (too modern)
- ❌ Large padding/spacing (Windows 95 was compact)

### React Anti-Patterns

#### Component Structure
- ❌ **Inline object/array creation in render** - Creates new references every render
  ```tsx
  // ❌ BAD
  <Component data={{ id: 1, name: 'test' }} />
  
  // ✅ GOOD
  const data = useMemo(() => ({ id: 1, name: 'test' }), []);
  <Component data={data} />
  ```

- ❌ **Using array index as key** - Can cause rendering issues
  ```tsx
  // ❌ BAD
  {items.map((item, i) => <div key={i}>...</div>)}
  
  // ✅ GOOD
  {items.map(item => <div key={item.id}>...</div>)}
  ```

- ❌ **Creating functions in render** - Creates new function every render
  ```tsx
  // ❌ BAD
  <button onClick={() => handleClick(id)}>Click</button>
  
  // ✅ GOOD
  const handleClick = useCallback((id) => { ... }, []);
  <button onClick={() => handleClick(id)}>Click</button>
  // OR
  <button onClick={handleClick.bind(null, id)}>Click</button>
  ```

#### State Management
- ❌ **Mutating state directly** - Always use setState
  ```tsx
  // ❌ BAD
  state.items.push(newItem);
  
  // ✅ GOOD
  setItems([...items, newItem]);
  ```

- ❌ **Storing derived state** - Calculate from props/state instead
  ```tsx
  // ❌ BAD
  const [sorted, setSorted] = useState([]);
  useEffect(() => setSorted(data.sort()), [data]);
  
  // ✅ GOOD
  const sorted = useMemo(() => [...data].sort(), [data]);
  ```

- ❌ **Using useState for computed values** - Use useMemo instead
  ```tsx
  // ❌ BAD
  const [total, setTotal] = useState(0);
  useEffect(() => setTotal(items.reduce(...)), [items]);
  
  // ✅ GOOD
  const total = useMemo(() => items.reduce(...), [items]);
  ```

#### Performance
- ❌ **Unnecessary re-renders** - Missing memoization
  ```tsx
  // ❌ BAD
  function ExpensiveComponent({ data }) {
    const processed = data.map(...); // Runs every render
    return <div>{processed}</div>;
  }
  
  // ✅ GOOD
  const ExpensiveComponent = memo(function ExpensiveComponent({ data }) {
    const processed = useMemo(() => data.map(...), [data]);
    return <div>{processed}</div>;
  });
  ```

- ❌ **Missing dependency arrays** - Causes stale closures
  ```tsx
  // ❌ BAD
  useEffect(() => {
    fetchData(id);
  }); // Missing dependencies
  
  // ✅ GOOD
  useEffect(() => {
    fetchData(id);
  }, [id]);
  ```

- ❌ **Not cleaning up effects** - Memory leaks
  ```tsx
  // ❌ BAD
  useEffect(() => {
    window.addEventListener('resize', handleResize);
  });
  
  // ✅ GOOD
  useEffect(() => {
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [handleResize]);
  ```

#### Event Handlers
- ❌ **Creating handlers in render without memoization**
  ```tsx
  // ❌ BAD
  function Component({ items }) {
    return items.map(item => (
      <button onClick={() => handleClick(item.id)}>{item.name}</button>
    ));
  }
  
  // ✅ GOOD
  function Component({ items }) {
    const handleClick = useCallback((id) => { ... }, []);
    return items.map(item => (
      <button onClick={() => handleClick(item.id)}>{item.name}</button>
    ));
  }
  ```

#### Props & Component Design
- ❌ **Prop drilling** - Passing props through many levels
  ```tsx
  // ❌ BAD
  <App><Parent><Child><Grandchild data={data} /></Child></Parent></App>
  
  // ✅ GOOD - Use context or state management
  ```

- ❌ **Too many props** - Component doing too much
  ```tsx
  // ❌ BAD
  <Component a={1} b={2} c={3} d={4} e={5} f={6} ... />
  
  // ✅ GOOD - Group related props or split component
  <Component config={config} />
  ```

- ❌ **Inline JSX conditionals** - Hard to read
  ```tsx
  // ❌ BAD
  {isLoading ? <Spinner /> : error ? <Error /> : data ? <Data /> : null}
  
  // ✅ GOOD
  if (isLoading) return <Spinner />;
  if (error) return <Error />;
  return <Data data={data} />;
  ```

## 📱 Responsive Considerations

- **Mobile**: Stack layouts, full-width charts
- **Tablet**: 2-column charts grid
- **Desktop**: Full sidebar + multi-column layouts
- **Breakpoints**: Use Tailwind defaults (sm, md, lg, xl)

## 🎨 Mood & Feel

The design should evoke:
- **Windows 95** - Classic beveled buttons, sharp corners
- **Excel 90s** - Grid-based layouts, classic gray/blue
- **Retro Computing** - Chunky borders, system fonts
- **Functional** - No frills, data-first
- **Nostalgic** - Reminds users of early Windows software
- **Professional** - Serious business application feel

Think: **Excel 95 meets Windows 95 File Manager**

### Key Visual Characteristics
- **Sharp corners everywhere** - No rounded borders
- **Beveled/raised borders** - 3D effect using light/dark borders
- **Classic gray palette** - #c0c0c0, #ffffff, #808080, #000000
- **Windows blue** - #000080 for selected/active states
- **System fonts** - MS Sans Serif aesthetic (or closest system font)
- **Compact spacing** - Windows 95 was tight, not spacious
- **Grid-based** - Everything aligns to a grid
- **Chunky borders** - 2px borders, not thin 1px

