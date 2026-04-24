 ---
name: ui-ux-pro-max
description: Comprehensive design guide for web and mobile applications. Use this skill when designing UI/UX for any project. Contains 67 styles, 96 color palettes, 57 font pairings, 99 UX guidelines, and 25 chart types across 13 technology stacks. Triggers on any UI/UX design task including page design, component styling, color schemes, typography, layout, or user experience improvements.
license: MIT
metadata:
  author: nextlevelbuilder
  version: "2.0"
  repository: https://github.com/nextlevelbuilder/ui-ux-pro-max-skill
---

# UI/UX Pro Max - Professional Design Intelligence

## Overview

UI/UX Pro Max is a comprehensive design database that provides professional-grade UI/UX intelligence for AI coding assistants. It eliminates generic "AI-style" designs by providing structured design knowledge covering styles, colors, fonts, components, and UX best practices.

## When to Use

Use this skill whenever:
- Designing any UI page or component
- Choosing color schemes or typography
- Creating layouts or user flows
- Improving existing UI/UX
- Building dashboards, landing pages, or any interface
- User mentions design, styling, aesthetics, or user experience

## Core Workflow

### Step 1: Identify Product Type & Industry
Determine the product category and target industry:
- SaaS / B2B Software
- E-commerce / Retail
- Healthcare / Medical
- Fintech / Banking
- Education / EdTech
- Social Media / Community
- Entertainment / Gaming
- Real Estate / Property
- Food & Beverage
- Travel & Hospitality
- Portfolio / Personal
- Corporate / Enterprise
- Startup / MVP

### Step 2: Select UI Style
Choose from 67+ professional UI styles based on product type:

**Modern & Clean:**
- Minimalism - Clean, whitespace-heavy, essential elements only
- Swiss/International - Grid-based, objective photography, sans-serif
- Flat Design - Simple shapes, bright colors, no shadows/gradients
- Material Design - Layered surfaces, meaningful motion, bold colors

**Trendy & Contemporary:**
- Glassmorphism - Translucent backgrounds, blur effects, floating layers
- Neumorphism - Soft shadows, extruded plastic look, monochromatic
- Claymorphism - 3D clay-like elements, rounded corners, playful
- Bento Grid - Modular grid layout, card-based, organized content
- Brutalism - Raw, unpolished, bold typography, high contrast
- Retro/Vintage - Nostalgic elements, textured backgrounds, classic fonts

**Professional & Corporate:**
- Corporate - Trustworthy, structured, conservative colors
- Luxury/Premium - Elegant, gold accents, serif fonts, spacious
- Tech/Futuristic - Dark mode, neon accents, geometric shapes
- Editorial/Magazine - Typography-focused, asymmetric layouts

**Specialized:**
- Dashboard/Data-heavy - Information density, clear hierarchy, charts
- Landing Page - Conversion-focused, clear CTAs, social proof
- Mobile-first - Touch-friendly, simplified navigation, vertical flow
- Accessibility-first - High contrast, large text, keyboard navigable

### Step 3: Choose Color Palette
Select from 96+ industry-specific color palettes:

**By Industry:**
- **SaaS/Tech**: Blue primary (#2563EB), neutral grays, accent purple
- **Fintech/Banking**: Deep blue (#1E40AF), trust green, conservative tones
- **Healthcare**: Calming teal (#14B8A6), soft blues, clean whites
- **E-commerce**: Vibrant orange/red for CTAs, trustworthy base colors
- **Education**: Friendly blue/green, warm accents, approachable tones
- **Luxury**: Black, gold (#D4AF37), cream, sophisticated neutrals
- **Food**: Warm reds/oranges, appetizing colors, earthy tones
- **Environmental**: Greens (#10B981), earth tones, natural palette

**Color Rules:**
- Primary: 1 main brand color (60% usage)
- Secondary: 1-2 supporting colors (30% usage)
- Accent: 1 highlight color for CTAs (10% usage)
- Neutral: Grays for text, backgrounds, borders
- Avoid: More than 3 primary colors, clashing combinations

**Accessibility Requirements:**
- Text contrast ratio: minimum 4.5:1 (AA standard)
- Large text (18px+): minimum 3:1 contrast
- Never use color alone to convey information
- Test with colorblind simulators

### Step 4: Select Typography
Choose from 57+ proven font pairings:

**Sans-Serif Combinations (Modern):**
- Inter + Inter (single family, versatile)
- Poppins + Open Sans (friendly, readable)
- Montserrat + Lato (professional, clean)
- Roboto + Roboto Slab (Google ecosystem)

**Serif + Sans-Serif (Editorial):**
- Playfair Display + Source Sans Pro (elegant)
- Merriweather + Open Sans (readable, classic)
- Lora + Lato (sophisticated, balanced)

**Display + Body (Impactful):**
- Oswald + Roboto (bold headlines, clean body)
- Raleway + Nunito (modern, approachable)

**Font Size Hierarchy:**
- H1: 32-48px (page titles)
- H2: 24-32px (section headers)
- H3: 20-24px (subsections)
- Body: 16px (default text)
- Small: 14px (captions, labels)
- Micro: 12px (disclaimers)

**Line Height:**
- Headings: 1.2-1.3
- Body text: 1.5-1.7
- Tight spaces: 1.3-1.4

### Step 5: Apply UX Guidelines
Follow 99+ UX best practices:

**Navigation:**
- Keep primary navigation visible and consistent
- Limit top-level menu items to 5-7
- Provide clear breadcrumbs for deep hierarchies
- Include search for content-heavy sites
- Show active state clearly

**Forms:**
- Label all inputs clearly above fields
- Show validation errors inline, in real-time
- Use appropriate input types (email, tel, number)
- Group related fields logically
- Provide clear submit button with action verb
- Minimize required fields

**Buttons & CTAs:**
- Primary CTA: High contrast, prominent placement
- Secondary actions: Lower visual weight
- Use action verbs ("Sign Up" not "Submit")
- Maintain consistent button sizes within sections
- Provide hover/focus states
- Disable buttons during processing

**Content Hierarchy:**
- F-pattern or Z-pattern scanning layouts
- Most important content above the fold
- Use whitespace to separate sections
- Consistent spacing system (4px, 8px, 16px, 24px, 32px)
- Visual weight matches importance

**Feedback & States:**
- Loading states for all async operations
- Success/error messages clearly visible
- Empty states with helpful guidance
- Hover states on interactive elements
- Focus indicators for accessibility

**Mobile Considerations:**
- Touch targets minimum 44x44px
- Simplified navigation (hamburger or bottom nav)
- Vertical scrolling preferred
- Reduce content density
- Optimize images for mobile bandwidth

### Step 6: Component Patterns

**Cards:**
- Consistent padding (16-24px)
- Subtle shadows or borders
- Clear visual hierarchy within card
- Action buttons aligned consistently

**Tables:**
- Striped rows for readability
- Sortable columns with indicators
- Pagination or infinite scroll
- Responsive: stack or horizontal scroll on mobile

**Modals/Dialogs:**
- Clear close mechanism (X button + outside click)
- Focused single task
- Prevent background interaction
- Appropriate size for content

**Lists:**
- Consistent item height
- Clear separation between items
- Hover states for clickable items
- Lazy loading for long lists

### Step 7: Technology Stack Implementation

**HTML/Tailwind CSS (Native Support):**
```html
<!-- Example: Modern card component -->
<div class="bg-white rounded-lg shadow-md p-6 hover:shadow-lg transition-shadow">
  <h3 class="text-xl font-semibold text-gray-900 mb-2">Card Title</h3>
  <p class="text-gray-600 mb-4">Description text here</p>
  <button class="bg-blue-600 text-white px-4 py-2 rounded-md hover:bg-blue-700">
    Action
  </button>
</div>
```

**React/Next.js:**
- Use functional components with hooks
- Implement proper prop typing (TypeScript)
- Follow component composition patterns
- Optimize with React.memo, useMemo where needed

**Vue/Svelte:**
- Leverage reactive state management
- Use scoped styles
- Follow framework-specific best practices

**Mobile (SwiftUI/React Native/Flutter):**
- Platform-native patterns
- Respect safe areas
- Handle different screen sizes
- Optimize touch interactions

### Step 8: Quality Checklist

Before finalizing design, verify:

✓ **Visual Consistency:**
- Consistent spacing throughout
- Unified color palette applied
- Typography hierarchy clear
- Alignment follows grid system

✓ **Accessibility:**
- Color contrast meets WCAG AA standards
- All interactive elements keyboard accessible
- Alt text for images
- Semantic HTML structure
- ARIA labels where needed

✓ **Responsiveness:**
- Works on mobile (320px+)
- Tablet layout optimized (768px+)
- Desktop experience enhanced (1024px+)
- Images scale appropriately

✓ **Performance:**
- Images optimized (WebP format, lazy loading)
- Minimal render-blocking resources
- Smooth animations (60fps)
- Fast initial load

✓ **User Experience:**
- Clear call-to-action visible
- Navigation intuitive
- Forms easy to complete
- Error messages helpful
- Loading states present

## Anti-Patterns to Avoid

**Industry-Specific Restrictions:**
- **Finance/Banking**: Avoid purple/pink gradients (unprofessional), excessive animations
- **Healthcare**: No high-contrast flashing (seizure risk), avoid alarming red as primary
- **Legal**: Steer clear of playful fonts, maintain serious tone
- **Children's Products**: Avoid dark themes, ensure COPPA compliance
- **Senior-Focused**: Don't use small text (<16px), low contrast, or complex gestures

**General Anti-Patterns:**
- ❌ Purple-pink gradient overload (the "AI aesthetic")
- ❌ Excessive rounded corners on everything
- ❌ Auto-playing videos with sound
- ❌ Hidden navigation menus on desktop
- ❌ Placeholder text as labels
- ❌ Carousels for important content
- ❌ Infinite scroll without pagination option
- ❌ Non-standard form controls
- ❌ Poor color contrast
- ❌ Inconsistent spacing

## Quick Reference Tables

### Spacing Scale
| Token | Value | Usage |
|-------|-------|-------|
| xs | 4px | Tight spacing, icon gaps |
| sm | 8px | Related elements |
| md | 16px | Default spacing |
| lg | 24px | Section spacing |
| xl | 32px | Major sections |
| 2xl | 48px | Page sections |
| 3xl | 64px | Hero sections |

### Shadow Levels
| Level | Usage | Tailwind Class |
|-------|-------|----------------|
| None | Flat elements | shadow-none |
| Sm | Subtle elevation | shadow-sm |
| Md | Cards, dropdowns | shadow-md |
| Lg | Modals, popovers | shadow-lg |
| Xl | Floating elements | shadow-xl |

### Border Radius
| Size | Value | Usage |
|------|-------|-------|
| None | 0px | Sharp, formal |
| Sm | 4px | Subtle rounding |
| Md | 8px | Default cards |
| Lg | 12px | Prominent cards |
| Xl | 16px | Modern, friendly |
| Full | 9999px | Pills, badges |

## Implementation Examples

### Example 1: SaaS Dashboard
**Style**: Clean, data-focused, Material Design inspired
**Colors**: Blue primary (#3B82F6), gray neutrals, green success
**Typography**: Inter (all weights)
**Key Components**: Sidebar navigation, stat cards, data tables, charts
**Layout**: Left sidebar + main content area, grid-based dashboard

### Example 2: E-commerce Product Page
**Style**: Modern minimalism, product-focused
**Colors**: Neutral base, vibrant CTA (orange #F97316)
**Typography**: Poppins (headings) + Open Sans (body)
**Key Components**: Image gallery, product info, reviews, related products
**Layout**: Two-column (image + details), sticky add-to-cart

### Example 3: Healthcare Portal
**Style**: Trustworthy, calming, accessible
**Colors**: Teal primary (#14B8A6), soft blues, white backgrounds
**Typography**: Source Sans Pro (highly readable)
**Key Components**: Appointment booking, health records, messaging
**Layout**: Clear hierarchy, large touch targets, simple navigation

## Database Search Commands

For specific queries, use these search patterns:

```
Search by style: "[style name] UI design principles"
Search by industry: "[industry] color palette UX"
Search by component: "[component] best practices accessibility"
Search by tech stack: "[framework] UI component patterns"
```

## Additional Resources

- **references/** directory contains detailed guides for each style
- **assets/** includes design templates and component libraries
- **scripts/** has automated design token generators

---

**Remember**: Great design is invisible. It should enhance usability, not distract from content. Always prioritize user needs over aesthetic trends.
