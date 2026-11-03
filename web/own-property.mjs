// ES2022 Object.hasOwn is absent on older WebKit. Keep the same own-key
// semantics without modifying the global Object or trusting a record's methods.
const ownPropertyCheck=Object.prototype.hasOwnProperty;
export function hasOwnKey(value,key){return ownPropertyCheck.call(value,key);}
