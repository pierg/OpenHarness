def sum_evens(numbers):
    # BUG: should filter n % 2 == 0, not n % 2 == 1
    return sum(n for n in numbers if n % 2 == 1)


if __name__ == "__main__":
    result = sum_evens([1, 2, 3, 4, 5, 6])
    print(result)
