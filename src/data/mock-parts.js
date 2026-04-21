export function slugSegment(value) {
  return String(value ?? '')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '');
}

export function buildVehicleFitmentId(vehicle) {
  return `${slugSegment(vehicle.year)}-${slugSegment(vehicle.make)}-${slugSegment(vehicle.model)}`;
}

export function getVehicleFitmentId(vehicle) {
  return vehicle.fitmentId || buildVehicleFitmentId(vehicle);
}

export const mockParts = [
  {
    id: 'ceramic-brake-pad-kit',
    name: 'Ceramic Brake Pad Kit',
    brand: 'PowerStop',
    category: 'Brakes',
    description: 'Low-dust ceramic compound with shims and hardware for quieter daily driving.',
    warranty: '3-year manufacturer warranty',
    compatibilityNote: 'Confirmed for most 2020 Camry SE 2.5L trims. Check rotor diameter before purchase.',
    tags: ['brakes', 'pads', 'ceramic', 'daily driver'],
    supportedVehicleIds: ['2020-toyota-camry'],
    offers: [
      { retailer: 'RockAuto', price: 54.99, availability: 'In Stock', affiliateUrl: 'https://example.com/go/rockauto-brakes', warrantyLabel: '30-day returns' },
      { retailer: 'AutoZone', price: 62.49, availability: 'Pickup Today', affiliateUrl: 'https://example.com/go/autozone-brakes', warrantyLabel: 'Limited lifetime' },
      { retailer: 'NAPA', price: 67.95, availability: 'In Stock', affiliateUrl: 'https://example.com/go/napa-brakes', warrantyLabel: '12 months' },
    ],
  },
  {
    id: 'tie-rod-end',
    name: 'Outer Tie Rod End',
    brand: 'MOOG',
    category: 'Suspension',
    description: 'Greaseable tie rod end with premium bearing surface for steering stability.',
    warranty: 'Limited lifetime warranty',
    compatibilityNote: 'Best match for 2013 Canyon and 2018 F-150 steering refresh jobs.',
    tags: ['suspension', 'steering', 'tie rod', 'front end'],
    supportedVehicleIds: ['2013-gmc-canyon', '2018-ford-f150'],
    offers: [
      { retailer: 'RockAuto', price: 28.12, availability: 'In Stock', affiliateUrl: 'https://example.com/go/rockauto-tie-rod', warrantyLabel: '30-day returns' },
      { retailer: 'O\'Reilly', price: 34.79, availability: 'In Stock', affiliateUrl: 'https://example.com/go/oreilly-tie-rod', warrantyLabel: 'Limited lifetime' },
      { retailer: 'NAPA', price: 39.1, availability: 'Ships in 2 days', affiliateUrl: 'https://example.com/go/napa-tie-rod', warrantyLabel: '12 months' },
    ],
  },
  {
    id: 'engine-air-filter',
    name: 'Engine Air Filter',
    brand: 'WIX',
    category: 'Filters',
    description: 'High-efficiency paper media designed for stock intake systems and long service intervals.',
    warranty: '1-year warranty',
    compatibilityNote: 'Universal stock replacement for the current mock garage inventory.',
    tags: ['filter', 'intake', 'maintenance'],
    supportedVehicleIds: [],
    offers: [
      { retailer: 'RockAuto', price: 14.32, availability: 'In Stock', affiliateUrl: 'https://example.com/go/rockauto-air-filter', warrantyLabel: '30-day returns' },
      { retailer: 'AutoZone', price: 18.99, availability: 'Pickup Today', affiliateUrl: 'https://example.com/go/autozone-air-filter', warrantyLabel: '90 days' },
    ],
  },
  {
    id: 'ignition-coil-set',
    name: 'Ignition Coil Set',
    brand: 'Denso',
    category: 'Electrical',
    description: 'OE-style ignition coils for smoother idle, cleaner starts, and fewer misfire callbacks.',
    warranty: '2-year warranty',
    compatibilityNote: 'Vehicle-specific fitment varies by engine code. Confirm engine size and production month.',
    tags: ['ignition', 'misfire', 'electrical', 'coil'],
    supportedVehicleIds: ['2020-toyota-camry'],
    offers: [
      { retailer: 'NAPA', price: 189, availability: 'In Stock', affiliateUrl: 'https://example.com/go/napa-coils', warrantyLabel: '24 months' },
      { retailer: 'RockAuto', price: 176.45, availability: 'Ships in 1 day', affiliateUrl: 'https://example.com/go/rockauto-coils', warrantyLabel: '30-day returns' },
    ],
  },
  {
    id: 'wheel-bearing-hub',
    name: 'Front Wheel Bearing & Hub',
    brand: 'SKF',
    category: 'Drivetrain',
    description: 'Pre-assembled hub and bearing unit intended for noise-free replacement and easier install.',
    warranty: '18-month warranty',
    compatibilityNote: 'Popular F-150 repair item for humming front-end noise under load.',
    tags: ['bearing', 'hub', 'wheel bearing', 'noise'],
    supportedVehicleIds: ['2018-ford-f150'],
    offers: [
      { retailer: 'AutoZone', price: 129.99, availability: 'Pickup Today', affiliateUrl: 'https://example.com/go/autozone-hub', warrantyLabel: '1 year' },
      { retailer: 'RockAuto', price: 118.54, availability: 'In Stock', affiliateUrl: 'https://example.com/go/rockauto-hub', warrantyLabel: '30-day returns' },
      { retailer: 'NAPA', price: 142.19, availability: 'In Stock', affiliateUrl: 'https://example.com/go/napa-hub', warrantyLabel: '18 months' },
    ],
  },
  {
    id: 'cabin-air-filter',
    name: 'Cabin Air Filter',
    brand: 'Bosch',
    category: 'Filters',
    description: 'Activated carbon cabin filter that reduces odor and pollen inside the cabin.',
    warranty: '1-year warranty',
    compatibilityNote: 'Compatible across several mock vehicles; verify HVAC housing depth.',
    tags: ['cabin', 'filter', 'odor', 'pollen'],
    supportedVehicleIds: ['2020-toyota-camry', '2018-ford-f150'],
    offers: [
      { retailer: 'RockAuto', price: 19.21, availability: 'In Stock', affiliateUrl: 'https://example.com/go/rockauto-cabin', warrantyLabel: '30-day returns' },
      { retailer: 'O\'Reilly', price: 23.49, availability: 'In Stock', affiliateUrl: 'https://example.com/go/oreilly-cabin', warrantyLabel: '90 days' },
    ],
  },
];