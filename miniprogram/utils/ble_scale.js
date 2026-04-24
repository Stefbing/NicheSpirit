function calculateMetrics(weight, impedance, user) {
  const heightM = user.height / 100
  const bmi = weight / (heightM * heightM)
  let bodyFat = user.gender === "male"
    ? 0.8 * bmi + 0.1 * user.age - 5.4
    : 0.8 * bmi + 0.1 * user.age + 4.1

  if (impedance > 0) {
    bodyFat += (impedance - 500) / 100
  }

  return {
    bmi: Number(bmi.toFixed(2)),
    bodyFat: Number(bodyFat.toFixed(2))
  }
}

module.exports = { calculateMetrics }
