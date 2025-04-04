# Practice

# We want to build a code that asks the user for inputs.
# First define what the context is and what the input will be used for.

# Exercise 1
# Area = length * width

# print("This is a triangle area calculator.")
# print("Please input the length and width of the triangle below.")
# length = int(input("Length:"))
# width = int(input("Width:"))
# unit = str(input("What unit are the dimensions measured in?:"))
# print("The area of the triangle is:",(length * width),unit,"^2")

# Exercise 2
# Hours to minutes
# 130 minutes = 2 hours and 10 minutes
# Hint: use // and %

# hour = int(input("Hours:  "))
# minute = hour * 60
# print(hour," hour(s)","=",minute,"minute(s).")

# Reverse

# print("This program will take minutes and convert to hours.")
# minutes = int(input("Minutes: "))
# hours = minutes//60
# h_minutes = (minutes % 60)
# print("This equates to:",hours,"hours and",h_minutes," minutes.")

# Exercise 3

# print("Give me a number and I'll determine whether it is even or odd.")
# num = int(input("Number: "))
# evodd = num%2
#
# if evodd == 0:
#     print("Even")
# else: print("Odd")

# For and While Loops Practice

# count = 0
# while count < 100:
#     print("Counting:", count)
#     count += 1

# while True:
#     user_input = input("Type 'exit' to quit: ")
#     if user_input == "exit":
#         break

# for i in range(1,11):
#     print(i)

# print even numbers in a range
# Hint: Use range(start, stop, step)

# for i in range(2,11,2):
#     print(i)

#loop through list of names

# names = ["Alice", "Bob", "Charlie"]
# # Output: Hello, Alice! etc.
#
# for i in names:
#     print("Hi, my name is",i)

# calculate sum of numbers 1-100
# Use a loop and a variable to keep track of the total
# total = 0
# for i in range(1,101):
#     total += i
# print("The sum of the total of range 1 to 100 is: ",total)

